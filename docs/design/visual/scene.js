/* Project scene for the component-design picture page: the river, the ford and the bridge. */
window.PROJECT_SCENE = ({ svg, el, css, trailY }) => {
  // river: a wavy band flowing top to bottom near x=560
  let left = "", right = "";
  for (let y = 0; y <= 460; y += 10) {
    const x = 560 + 22 * Math.sin(y / 46);
    left += (y ? " L" : "M") + (x - 30) + "," + y;
    right = " L" + (x + 30) + "," + y + right;
  }
  el("path", { d: left + right + " Z", fill: css("--accent-soft"), stroke: css("--accent"), "stroke-width": 2, filter: "url(#rough)" }, svg);
  el("text", { x: 600, y: 448, class: "hand", "font-size": 18, fill: css("--accent") }, svg).textContent = "the river (the main street)";

  // the usual path: straight across at the ford
  el("path", { d: `M30,${trailY} C220,${trailY - 30} 380,${trailY + 20} 530,${trailY} L590,${trailY} C700,${trailY - 20} 840,${trailY + 25} 975,${trailY - 10}`, fill: "none", stroke: css("--trail"), "stroke-width": 3, "stroke-dasharray": "9 7", filter: "url(#rough)", "marker-end": "url(#arrow)" }, svg);
  // ford marker
  el("circle", { cx: 560, cy: trailY, r: 15, fill: css("--card"), stroke: css("--ford"), "stroke-width": 2.5, filter: "url(#rough)" }, svg);
  el("text", { x: 560, y: trailY + 7, "text-anchor": "middle", class: "hand", "font-size": 22, "font-weight": 700, fill: css("--ford") }, svg).textContent = "!";
  el("text", { x: 560, y: trailY + 44, "text-anchor": "middle", class: "hand", "font-size": 16, fill: css("--ford") }, svg).textContent = "the ford: wait for a gap";
  // detour to the bridge upstream
  el("path", { d: `M420,${trailY - 4} C440,150 470,84 520,72 L600,72 C660,82 690,150 710,${trailY - 8}`, fill: "none", stroke: css("--bridge"), "stroke-width": 2.5, "stroke-dasharray": "4 6", filter: "url(#rough)" }, svg);
  for (let i = 0; i < 6; i++) el("line", { x1: 524 + i * 14, y1: 62, x2: 524 + i * 14, y2: 82, stroke: css("--bridge"), "stroke-width": 2.5, filter: "url(#rough)" }, svg);
  el("text", { x: 560, y: 46, "text-anchor": "middle", class: "hand", "font-size": 16, fill: css("--bridge") }, svg).textContent = "the bridge upstream ✓";
};

/* Keep the station that would sit in the river on the near bank, below the trail. */
window.PROJECT_PLACE = ({ x, y, up, trailY }) => {
  if (x > 470 && x < 530) return { x: x - 70, y: trailY + 70, up: false };
  if (x > 620 && x < 680) return { x: 770, y, up }; // clear of the bridge
  return null;
};
