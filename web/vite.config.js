import { defineConfig } from 'vite';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// public/data holds ~240k streamed tiles and keeps growing. Watching it exhausts inotify
// (fs.inotify.max_user_watches), after which tmp+rename tile writes drop out of Vite's
// publicFiles set and get the index.html fallback. So: don't watch public/data, and serve
// /data/* straight from disk with a per-request stat (missing -> 404, never HTML).
const root = path.dirname(fileURLToPath(import.meta.url));
const dataDir = path.resolve(root, 'public/data');
const TYPES = { '.bin': 'application/octet-stream', '.json': 'application/json', '.wkt': 'text/plain; charset=utf-8', '.txt': 'text/plain; charset=utf-8', '.png': 'image/png' };

function dataFromDisk() {
  return {
    name: 'data-from-disk',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.method !== 'GET' && req.method !== 'HEAD') return next();
        let p;
        try { p = decodeURIComponent((req.url || '').split(/[?#]/)[0]); } catch { res.statusCode = 400; return res.end(); }
        if (!p.startsWith('/data/')) return next();
        const file = path.resolve(dataDir, '.' + p.slice('/data'.length));
        const fail = (code) => { res.statusCode = code; res.setHeader('Content-Type', 'text/plain; charset=utf-8'); res.end(code === 404 ? 'not found' : 'forbidden'); };
        if (file !== dataDir && !file.startsWith(dataDir + path.sep)) return fail(403);
        fs.stat(file, (err, st) => {
          if (err || !st.isFile()) return fail(404);
          res.statusCode = 200;
          res.setHeader('Content-Type', TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream');
          res.setHeader('Content-Length', st.size);
          res.setHeader('Cache-Control', 'no-cache');
          if (req.method === 'HEAD') return res.end();
          const s = fs.createReadStream(file);
          s.on('error', () => { if (!res.headersSent) fail(404); else res.destroy(); });
          s.pipe(res);
        });
      });
    },
  };
}

export default defineConfig({
  base: './',
  plugins: [dataFromDisk()],
  server: { watch: { ignored: [dataDir + '/**', '**/public/data/**'] } },
  worker: { format: 'es' },
  build: { chunkSizeWarningLimit: 2000 },
});
