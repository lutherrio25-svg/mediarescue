// Read a PNG (8-bit RGBA/RGB, no interlace) and print it as ASCII, so a 16 px icon can be judged
// pixel by pixel instead of by eye on an antialiased thumbnail. Also reports how many pixels are
// partially transparent, which is the tell for stray antialiasing at small sizes.
import fs from "node:fs";
import zlib from "node:zlib";

const file = process.argv[2];
const buf = fs.readFileSync(file);
let off = 8;
let ihdr = null;
const idat = [];
while (off < buf.length) {
  const len = buf.readUInt32BE(off);
  const type = buf.toString("ascii", off + 4, off + 8);
  const data = buf.subarray(off + 8, off + 8 + len);
  if (type === "IHDR")
    ihdr = {
      w: data.readUInt32BE(0),
      h: data.readUInt32BE(4),
      depth: data[8],
      color: data[9],
      interlace: data[12],
    };
  if (type === "IDAT") idat.push(data);
  off += 12 + len;
}
if (!ihdr) throw new Error("no IHDR");
const ch = { 0: 1, 2: 3, 4: 2, 6: 4 }[ihdr.color];
if (ihdr.depth !== 8 || ihdr.interlace) throw new Error(`unsupported png: depth ${ihdr.depth}`);
const raw = zlib.inflateSync(Buffer.concat(idat));
const stride = ihdr.w * ch;
const px = Buffer.alloc(ihdr.h * stride);
const paeth = (a, b, c) => {
  const p = a + b - c;
  const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
  return pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
};
for (let y = 0; y < ihdr.h; y++) {
  const filter = raw[y * (stride + 1)];
  const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
  for (let x = 0; x < stride; x++) {
    const a = x >= ch ? px[y * stride + x - ch] : 0;
    const b = y > 0 ? px[(y - 1) * stride + x] : 0;
    const c = x >= ch && y > 0 ? px[(y - 1) * stride + x - ch] : 0;
    const v = line[x];
    px[y * stride + x] =
      filter === 0 ? v : filter === 1 ? (v + a) & 255 : filter === 2 ? (v + b) & 255
      : filter === 3 ? (v + ((a + b) >> 1)) & 255 : (v + paeth(a, b, c)) & 255;
  }
}

const ramp = " .:-=+*#%@";
let partial = 0;
const rows = [];
for (let y = 0; y < ihdr.h; y++) {
  let line = "";
  for (let x = 0; x < ihdr.w; x++) {
    const i = y * stride + x * ch;
    const [r, g, b, a] = ch === 4 ? [px[i], px[i + 1], px[i + 2], px[i + 3]] : [px[i], px[i + 1], px[i + 2], 255];
    if (a !== 0 && a !== 255) partial++;
    const lum = 0.299 * r + 0.587 * g + 0.114 * b;
    const v = (lum / 255) * (a / 255);
    line += ramp[Math.min(ramp.length - 1, Math.round(v * (ramp.length - 1)))];
  }
  rows.push(line);
}
console.log(`${file} ${ihdr.w}x${ihdr.h} channels=${ch}`);
console.log(`partially transparent pixels: ${partial}`);
console.log(rows.join("\n"));
