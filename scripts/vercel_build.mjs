// Builds the static UI for Vercel. The API runs elsewhere (Render), so this points the UI at it
// and allows exactly that origin in the page's Content-Security-Policy.
import { cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';

const api = (process.env.API_BASE_URL || '').replace(/\/+$/, '');
if (!/^https:\/\/[^/\s]+$/.test(api)) {
  console.error('Set API_BASE_URL to the https origin of your API, e.g. https://research-desk.onrender.com');
  process.exit(1);
}

rmSync('dist', { recursive: true, force: true });
mkdirSync('dist');
cpSync('app/static', 'dist', { recursive: true });
writeFileSync('dist/config.js', `window.RD_API_BASE = ${JSON.stringify(api)};\n`);

const csp = `default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self' ${api}; base-uri 'none'; form-action 'none'`;
const html = readFileSync('dist/index.html', 'utf8').replace(
  '<meta name="color-scheme"',
  `<meta http-equiv="Content-Security-Policy" content="${csp}">\n  <meta name="color-scheme"`,
);
writeFileSync('dist/index.html', html);
console.log(`UI built for ${api}`);
