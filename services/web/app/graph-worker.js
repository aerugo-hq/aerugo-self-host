// The knowledge map's physics off the main thread, for graphs over 2,000 nodes. The page's
// requestAnimationFrame stays the only clock: it asks for one step per frame and draws the reply.
import { createPhysics } from "./graph-physics.js";

let physics = null;
self.onmessage = ({ data: m }) => {
  if (m.type === "init") {
    physics = createPhysics(m.options);
    return;
  }
  if (!physics) return;
  if (m.type === "step") {
    const moving = physics.step(m.now);
    const xy = physics.positions();
    self.postMessage({ type: "tick", xy, moving }, [xy.buffer]);
  } else if (m.type === "visible") physics.setVisible(m.mask);
  else if (m.type === "dragStart") physics.dragStart(m.i);
  else if (m.type === "drag") physics.drag(m.i, m.x, m.y);
  else if (m.type === "dragEnd") physics.dragEnd(m.i);
};
