// Minimal PNG reader for 8-bit RGB/RGBA, non-interlaced — enough to turn a Chrome screenshot into
// the raw pixels an .ico needs. Returns straight (non-premultiplied) RGBA.
import fs from "node:fs";
import zlib from "node:zlib";

function paeth(a, b, c) {
  const p = a + b - c;
  const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
  return pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
}

export function readPng(file) {
  const buf = fs.readFileSync(file);
  let off = 8;
  let ihdr = null;
  const idat = [];
  while (off < buf.length) {
    const len = buf.readUInt32BE(off);
    const type = buf.toString("ascii", off + 4, off + 8);
    const data = buf.subarray(off + 8, off + 8 + len);
    if (type === "IHDR")
      ihdr = { w: data.readUInt32BE(0), h: data.readUInt32BE(4), depth: data[8], color: data[9], interlace: data[12] };
    if (type === "IDAT") idat.push(data);
    off += 12 + len;
  }
  if (!ihdr) throw new Error(`${file}: no IHDR`);
  const ch = { 0: 1, 2: 3, 4: 2, 6: 4 }[ihdr.color];
  if (ihdr.depth !== 8 || ihdr.interlace || !ch) throw new Error(`${file}: unsupported png`);
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const stride = ihdr.w * ch;
  const px = Buffer.alloc(ihdr.h * stride);
  for (let y = 0; y < ihdr.h; y++) {
    const filter = raw[y * (stride + 1)];
    const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let x = 0; x < stride; x++) {
      const a = x >= ch ? px[y * stride + x - ch] : 0;
      const b = y > 0 ? px[(y - 1) * stride + x] : 0;
      const c = x >= ch && y > 0 ? px[(y - 1) * stride + x - ch] : 0;
      const v = line[x];
      px[y * stride + x] =
        filter === 0 ? v
        : filter === 1 ? (v + a) & 255
        : filter === 2 ? (v + b) & 255
        : filter === 3 ? (v + ((a + b) >> 1)) & 255
        : (v + paeth(a, b, c)) & 255;
    }
  }
  const rgba = Buffer.alloc(ihdr.w * ihdr.h * 4);
  for (let i = 0, n = ihdr.w * ihdr.h; i < n; i++) {
    const s = i * ch;
    const d = i * 4;
    if (ch >= 3) {
      rgba[d] = px[s];
      rgba[d + 1] = px[s + 1];
      rgba[d + 2] = px[s + 2];
      rgba[d + 3] = ch === 4 ? px[s + 3] : 255;
    } else {
      rgba[d] = rgba[d + 1] = rgba[d + 2] = px[s];
      rgba[d + 3] = ch === 2 ? px[s + 1] : 255;
    }
  }
  return { w: ihdr.w, h: ihdr.h, rgba };
}
