import * as THREE from 'three';
import type { Meta } from './types';
import { fetchGzip } from './util';
import type { VoxelUniforms } from './voxelMaterial';

/** Metres / texels on a side. Must stay ≤ typical WebGL MAX_TEXTURE_SIZE (8192). */
export const RASTER_CHUNK = 2048;

export interface RasterIndex {
  s: number;
  nx: number;
  ny: number;
  x0: number;
  y0: number;
  RW: number;
  RH: number;
}

export function rasterIndexFromMeta(meta: Meta): RasterIndex {
  const rc = meta.raster_chunks;
  if (rc?.s && rc.nx && rc.ny) return rc;
  const x0 = meta.x0 ?? 0, y0 = meta.y0 ?? 0, RW = meta.W - x0, RH = meta.D - y0;
  const s = RASTER_CHUNK;
  return { s, nx: Math.ceil(RW / s), ny: Math.ceil(RH / s), x0, y0, RW, RH };
}

export function bindRasterUniforms(U: VoxelUniforms, index: RasterIndex) {
  U.uRasCS.value = index.s;
  U.uRasNx.value = index.nx;
  if (U.uRasSize) U.uRasSize.value.set(index.RW, index.RH);
  // Style maps (zones / windows / foliage.bin / terrain.bin) keep this origin,
  // which may be smaller than the streamed tile window (WebGL 8192 + memory).
  if (U.uMap0) U.uMap0.value.set(index.x0, index.y0);
}

function arrayTexture(
  data: Uint8Array | Uint16Array,
  w: number, h: number, depth: number,
  format: THREE.PixelFormat,
  type: THREE.TextureDataType = THREE.UnsignedByteType,
  internalFormat?: THREE.PixelFormatGPU,
) {
  const t = new THREE.DataArrayTexture(data, w, h, depth);
  t.format = format;
  t.type = type;
  if (internalFormat) t.internalFormat = internalFormat;
  t.unpackAlignment = (data as { BYTES_PER_ELEMENT?: number }).BYTES_PER_ELEMENT ?? 1;
  t.minFilter = t.magFilter = THREE.NearestFilter;
  t.generateMipmaps = false;
  t.needsUpdate = true;
  t.colorSpace = THREE.NoColorSpace;
  return t;
}

export function tinyRasterArray(
  format: THREE.PixelFormat,
  data: Uint8Array | Uint16Array,
  type?: THREE.TextureDataType,
  internalFormat?: THREE.PixelFormatGPU,
) {
  return arrayTexture(data, 1, 1, 1, format, type, internalFormat);
}

/** Load every in-window chunk into a 2D array texture (layer = cy*nx+cx).
 *  A modest window is a few dozen 2048² layers — well under GPU memory.
 *  When the map later has hundreds of chunks, page the same array by
 *  rebinding layers around the camera; the shader already addresses by (cx,cy). */
async function fetchChunks(kind: 'zones' | 'windows', index: RasterIndex) {
  const { s, nx, ny } = index;
  const jobs: Promise<{ i: number; buf: ArrayBuffer }>[] = [];
  for (let cy = 0; cy < ny; cy++) for (let cx = 0; cx < nx; cx++) {
    const i = cy * nx + cx;
    jobs.push(fetchGzip(`data/${kind}/${cx}_${cy}.bin`).then((buf) => ({ i, buf })));
  }
  return { s, nx, ny, layers: ny * nx, files: await Promise.all(jobs) };
}

export async function loadZoneChunks(meta: Meta) {
  const index = rasterIndexFromMeta(meta);
  const { s, nx, layers, files } = await fetchChunks('zones', index);
  const blk = new Uint16Array(s * s * layers);
  const bld = new Uint16Array(index.RH * index.RW);
  const cell = s * s;
  for (const { i, buf } of files) {
    const u = new Uint16Array(buf);
    if (u.length < cell * 2) throw new Error(`zones chunk ${i} short (${u.length})`);
    blk.set(u.subarray(0, cell), i * cell);
    const cy = Math.floor(i / nx), cx = i % nx;
    const r0 = cy * s, c0 = cx * s;
    const rh = Math.min(s, index.RH - r0), rw = Math.min(s, index.RW - c0);
    const src = u.subarray(cell, cell * 2);
    for (let r = 0; r < rh; r++) {
      bld.set(src.subarray(r * s, r * s + rw), (r0 + r) * index.RW + c0);
    }
  }
  const blkTex = arrayTexture(blk, s, s, layers, THREE.RedIntegerFormat, THREE.UnsignedShortType, 'R16UI');
  return { index, blkTex, bld, RW: index.RW, RH: index.RH };
}

export async function loadWindowChunks(meta: Meta) {
  const index = rasterIndexFromMeta(meta);
  const { s, layers, files } = await fetchChunks('windows', index);
  const data = new Uint8Array(s * s * layers);
  const cell = s * s;
  for (const { i, buf } of files) {
    const u = new Uint8Array(buf);
    if (u.length < cell) throw new Error(`windows chunk ${i} short (${u.length})`);
    data.set(u.subarray(0, cell), i * cell);
  }
  const tex = arrayTexture(data, s, s, layers, THREE.RedFormat);
  return { index, tex };
}

