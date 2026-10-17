"use strict";
// Used only by the pinned parser's stringify(block) call without options.
// Preserve its value concatenation while avoiding recursive AST walking.
module.exports = ast => {
    const pending = [ast];
    let output = "";
    while (pending.length) {
        const node = pending.pop();
        if (node.value) output += node.value;
        else if (node.nodes) {
            for (let index = node.nodes.length - 1; index >= 0; index--) {
                pending.push(node.nodes[index]);
            }
        }
    }
    return output;
};
