// mediarescue app icon — one geometry module, every deliverable grows from these numbers.
// Render: node packaging/brand/source/render.mjs
// Palette is deliberately small and value-separated: the mark must read at 16 px and inside a
// circular crop (Windows shows the tile in the taskbar, Start menu and Add/Remove Programs).

export const palette = {
  ink: "#0B1220", // tile
  cream: "#F4EFE6", // play triangle: brightest thing on the tile
  amber: "#F2A03D", // the repair accent
  teal: "#2FD4C4", // alternative accent, kept so the choice is one constant away
};

const S = 256; // design grid; every number below is in these units

/** Play triangle, optically centred: the visual centre of a triangle sits left of its bounding box. */
const tri = { x0: 86, yTop: 52, yBottom: 204, xTip: 196 };
const triPoints = [
  [tri.x0, tri.yTop],
  [tri.x0, tri.yBottom],
  [tri.xTip, S / 2],
];

function plus(cx, cy, arm, weight, fill) {
  // A cross drawn as two rects with the same weight, so it never turns into a blob when scaled.
  const h = arm * 2;
  return `
    <rect x="${cx - arm}" y="${cy - weight / 2}" width="${h}" height="${weight}" rx="${weight * 0.28}" fill="${fill}"/>
    <rect x="${cx - weight / 2}" y="${cy - arm}" width="${weight}" height="${h}" rx="${weight * 0.28}" fill="${fill}"/>`;
}

/** Fix patch: a rounded band clipped to the tile, sitting behind the triangle like a mended strip. */
function stripe(angle, fill) {
  return `<rect x="-64" y="150" width="${S + 128}" height="46" fill="${fill}"
    transform="rotate(${angle} ${S / 2} ${S / 2})" opacity="0.22"/>`;
}

export function mark({ variant = "plus", accent = palette.amber, rounded = 56, size = 0 } = {}) {
  const shapes = [];
  if (variant === "patch") shapes.push(stripe(-18, accent));
  if (variant === "shield") {
    // Shield = rescue; the play triangle is knocked out of it, so the tile colour is the second shape.
    // Symmetric silhouette with one strong negative shape is what survives 16 px.
    // Below 24 px the cut-out is only a few device pixels wide, so the glyph is grown a little to
    // keep the notch readable instead of letting it blur into the shield.
    // Small sizes are not the same drawing scaled down: the cut-out needs proportionally more room
    // and the tile needs to separate from a dark taskbar, so both are nudged per size.
    const boost = size && size <= 16 ? 0.24 : size && size <= 20 ? 0.18 : size && size <= 24 ? 0.10 : 0;
    const cx = (104 + 104 + 166) / 3;
    const cy = (92 + 164 + 128) / 3;
    const grow = (x, y) => `${(cx + (x - cx) * (1 + boost)).toFixed(1)},${(cy + (y - cy) * (1 + boost)).toFixed(1)}`;
    const glyph = [grow(100, 88), grow(100, 168), grow(172, 128)].join(" ");
    shapes.push(
      `<path d="M128 34 L206 62 V132 C206 172 172 202 128 220 C84 202 50 172 50 132 V62 Z"
        fill="${palette.cream}" opacity="0.98"/>`,
      `<polygon points="${glyph}" fill="${palette.ink}"/>`
    );
  } else {
    shapes.push(
      `<polygon points="${triPoints.map((p) => p.join(",")).join(" ")}" fill="${palette.cream}"/>`
    );
    if (variant === "plus") shapes.push(plus(196, 196, 30, 22, accent));
    if (variant === "patch")
      shapes.push(
        `<rect x="150" y="150" width="76" height="76" rx="18" fill="${accent}" opacity="0.95"/>`
      );
  }
  // A #0B1220 tile vanishes against a dark taskbar; small sizes get a lighter tile so the rounded
  // square keeps a silhouette. Large sizes stay ink, where the cream shield carries the contrast.
  const tile = size && size <= 20 ? "#16233A" : palette.ink;
  return `
  <rect width="${S}" height="${S}" rx="${rounded}" fill="${tile}"/>
  ${shapes.join("\n  ")}`;
}

/** 16 px is drawn on its own 16-unit grid with integer coordinates: the scale-down of the big mark
 *  puts the cut-out on half pixels and turns it into a smudge. Every number here is a whole pixel. */
function svg16() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16"
    shape-rendering="crispEdges">
    <rect width="16" height="16" rx="3" fill="#16233A"/>
    <polygon points="3,2 13,2 13,8 8,14 3,8" fill="${palette.cream}"/>
    <polygon points="6,5 6,11 10,8" fill="#16233A"/>
  </svg>`;
}

export function svg({ size = 512, variant, accent, rounded } = {}) {
  if (size && size <= 16 && variant === "shield") return svg16();
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}"
    viewBox="0 0 ${S} ${S}" shape-rendering="geometricPrecision">${mark({ variant, accent, rounded, size })}</svg>`;
}
