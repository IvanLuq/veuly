// Copies KaTeX's stylesheet and its woff2 fonts next to the bundle (the app works offline).
const fs = require('fs'), path = require('path');
const src = path.join(__dirname, 'node_modules', 'katex', 'dist'), dest = path.join(__dirname, '..', 'web', 'vendor', 'katex');
fs.mkdirSync(path.join(dest, 'fonts'), { recursive: true });
fs.copyFileSync(path.join(src, 'katex.min.css'), path.join(dest, 'katex.min.css'));
for (const f of fs.readdirSync(path.join(src, 'fonts'))) if (f.endsWith('.woff2')) fs.copyFileSync(path.join(src, 'fonts', f), path.join(dest, 'fonts', f));
