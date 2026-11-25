// Render the icon variants to PNG (headless Chrome) and pack them into .ico files.
// No Pillow, no sharp: ICO here is just a directory of PNG payloads, which is what Vista+ reads.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { palette, svg } from "./make.mjs";
import { readPng } from "./pngread.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.join(here, "..", "out");
fs.mkdirSync(out, { recursive: true });

const CHROME = [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
].find((p) => fs.existsSync(p));
if (!CHROME) throw new Error("no Chrome or Edge found");

const SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256];
const VARIANTS = [
  { name: "a-plus-amber", opts: { variant: "plus", accent: palette.amber } },
  { name: "b-patch-amber", opts: { variant: "patch", accent: palette.amber } },
  { name: "c-plus-teal", opts: { variant: "plus", accent: palette.teal } },
  { name: "d-shield", opts: { variant: "shield" } },
];

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "mricon-"));

function shootHtml(html, w, h, file) {
  const page = path.join(tmp, `${path.basename(file, ".png")}.html`);
  fs.writeFileSync(page, html, "utf8");
  execFileSync(
    CHROME,
    [
      "--headless=new",
      "--disable-gpu",
      "--hide-scrollbars",
      "--force-device-scale-factor=1",
      "--default-background-color=00000000",
      `--window-size=${w},${h}`,
      `--screenshot=${file}`,
      `file:///${page.replace(/\\/g, "/")}`,
    ],
    { stdio: "ignore" }
  );
  if (!fs.existsSync(file)) throw new Error(`chrome produced nothing for ${file}`);
}

function shoot(name, opts, size, file) {
  shootHtml(
    `<!doctype html><meta charset="utf-8"><style>
       html,body{margin:0;padding:0;background:transparent;overflow:hidden}
       svg{display:block}
     </style>${svg({ size, ...opts })}`,
    size,
    size,
    file
  );
}

function bmpFrame(size, pngFile) {
  // Classic DIB frame: 32 bpp BGRA, bottom-up, then the 1bpp AND mask. GDI+ refuses PNG entries, so
  // a PNG-only ico makes Icon.ToBitmap() throw "Requested range extends past the end of the array".
  const { w, h, rgba } = readPng(pngFile);
  if (w !== size || h !== size) throw new Error(`expected ${size}px, got ${w}x${h}`);
  const header = Buffer.alloc(40);
  header.writeUInt32LE(40, 0);
  header.writeInt32LE(size, 4);
  header.writeInt32LE(size * 2, 8); // height doubles: XOR image + AND mask
  header.writeUInt16LE(1, 12);
  header.writeUInt16LE(32, 14);
  header.writeUInt32LE(size * size * 4, 20);
  const pixels = Buffer.alloc(size * size * 4);
  for (let y = 0; y < size; y++) {
    const srcRow = (size - 1 - y) * size * 4; // bottom-up
    for (let x = 0; x < size; x++) {
      const s = srcRow + x * 4;
      const d = (y * size + x) * 4;
      pixels[d] = rgba[s + 2]; // B
      pixels[d + 1] = rgba[s + 1]; // G
      pixels[d + 2] = rgba[s]; // R
      pixels[d + 3] = rgba[s + 3]; // A
    }
  }
  const maskStride = Math.ceil(size / 32) * 4;
  const mask = Buffer.alloc(maskStride * size);
  for (let y = 0; y < size; y++) {
    const srcRow = (size - 1 - y) * size * 4;
    for (let x = 0; x < size; x++) {
      if (rgba[srcRow + x * 4 + 3] === 0) mask[y * maskStride + (x >> 3)] |= 0x80 >> (x & 7);
    }
  }
  return Buffer.concat([header, pixels, mask]);
}

function packIco(frames, dest) {
  // ICONDIR + one ICONDIRENTRY per frame, each pointing at a complete image payload.
  const head = Buffer.alloc(6);
  head.writeUInt16LE(1, 2); // 1 = icon
  head.writeUInt16LE(frames.length, 4);
  const entries = Buffer.alloc(16 * frames.length);
  let offset = 6 + 16 * frames.length;
  const blobs = [];
  frames.forEach(({ size, data }, i) => {
    const at = i * 16;
    entries.writeUInt8(size >= 256 ? 0 : size, at);
    entries.writeUInt8(size >= 256 ? 0 : size, at + 1);
    entries.writeUInt16LE(1, at + 4); // planes
    entries.writeUInt16LE(32, at + 6); // bpp
    entries.writeUInt32LE(data.length, at + 8);
    entries.writeUInt32LE(offset, at + 12);
    offset += data.length;
    blobs.push(data);
  });
  fs.writeFileSync(dest, Buffer.concat([head, entries, ...blobs]));
  return offset;
}

for (const v of VARIANTS) {
  const pngs = [];
  for (const size of SIZES) {
    const file = path.join(out, `${v.name}-${size}.png`);
    shoot(v.name, v.opts, size, file);
    pngs.push({ size, data: fs.readFileSync(file) });
  }
  const ico = path.join(out, `${v.name}.ico`);
  const frames = pngs.map(({ size, data }) =>
    size >= 256 ? { size, data } : { size, data: bmpFrame(size, path.join(out, `${v.name}-${size}.png`)) }
  );
  const bytes = packIco(frames, ico);
  console.log(`${v.name}: ${SIZES.length} pngs, ${ico} (${(bytes / 1024).toFixed(1)} KB)`);
}

// One readable sheet per variant at 512 px, plus a contact sheet: every real size on both a light
// and a dark surface, because the taskbar is dark and Explorer is usually light.
for (const v of VARIANTS) {
  shoot(v.name, v.opts, 512, path.join(out, `${v.name}-512.png`));
  const cells = [16, 20, 24, 32, 48, 64, 128]
    .map((s) => `<div class="cell">${svg({ size: s, ...v.opts })}<span>${s}</span></div>`)
    .join("");
  const sheet = `<!doctype html><meta charset="utf-8"><style>
    body{margin:0;font:11px system-ui;font-family:Segoe UI,sans-serif}
    .row{display:flex;flex-wrap:wrap;align-items:flex-end;gap:18px;padding:16px 18px}
    .light{background:#f7f7f8;color:#333}
    .dark{background:#101720;color:#ccc}
    .cell{display:flex;flex-direction:column;align-items:center;gap:6px}
  </style>
  <div class="row light">${cells}</div><div class="row dark">${cells}</div>`;
  shootHtml(sheet, 520, 400, path.join(out, `sheet-${v.name}.png`));
}
// 16 px icons are inspected at 1:1 by nobody: blow them up with nearest-neighbour so the pixel grid
// (and any half-pixel mush) is visible when we judge them.
for (const v of VARIANTS) {
  const cells = [16, 20, 24]
    .map((sz) => {
      const src = `file:///${path.join(out, `${v.name}-${sz}.png`).replace(/\\/g, "/")}`;
      return `<div class="cell"><img src="${src}" style="width:${sz * 10}px;height:${sz * 10}px;image-rendering:pixelated"><span>${sz}px ×10</span></div>`;
    })
    .join("");
  const zoom = `<!doctype html><meta charset="utf-8"><style>
    body{margin:0;background:#101720;color:#bbb;font:11px Segoe UI,sans-serif}
    .row{display:flex;align-items:flex-end;gap:26px;padding:18px}
    .cell{display:flex;flex-direction:column;align-items:center;gap:6px}
    img{display:block}
  </style><div class="row">${cells}</div>`;
  shootHtml(zoom, 740, 320, path.join(out, `zoom-${v.name}.png`));
}
// Hero sheet: how the icon sits next to the wordmark, which is the way it will actually be seen.
{
  const opts = { variant: "shield" };
  const sizes = [16, 24, 32, 48, 64]
    .map((sz) => `<div class="cell">${svg({ size: sz, ...opts })}<span>${sz}</span></div>`)
    .join("");
  const html = `<!doctype html><meta charset="utf-8"><style>
    body{margin:0;background:radial-gradient(120% 140% at 0% 0%,#182642 0%,#0B1220 60%);color:${palette.cream};
         font-family:Segoe UI,system-ui,sans-serif}
    .wrap{display:flex;align-items:center;gap:34px;padding:38px 44px}
    .word{display:flex;flex-direction:column;gap:8px}
    h1{margin:0;font-size:44px;font-weight:600;letter-spacing:-0.5px}
    .sub{color:#8A94A6;font-size:15px;letter-spacing:2px;text-transform:uppercase}
    .sizes{display:flex;align-items:flex-end;gap:18px;margin-top:16px;background:#F7F7F8;
           border-radius:14px;padding:12px 16px;align-self:flex-start;color:#4A5468}
    .cell{display:flex;flex-direction:column;align-items:center;gap:5px;font-size:11px;color:#6C7688}
  </style><div class="wrap">
    ${svg({ size: 200, ...opts })}
    <div class="word"><h1>mediarescue</h1><div class="sub">Star Corps bks</div>
      <div class="sizes">${sizes}</div></div>
  </div>`;
  shootHtml(html, 800, 300, path.join(out, "hero.png"));
}
console.log("done");
