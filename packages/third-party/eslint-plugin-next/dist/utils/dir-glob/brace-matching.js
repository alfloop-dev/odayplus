"use strict";
// The pinned fast-glob3.3.1 implementation uses exactly these three functions.
// Preserve its picomatch2.3.2 scan/regex semantics and use a maintained expander.
const picomatch = require("picomatch");
const expandBraces = require("brace-expansion");
const parseRangeTree = require("./brace-range-parser/parse");
module.exports = {
    scan: (pattern, options) => picomatch.scan(pattern, options),
    makeRe: (pattern, options) => picomatch.makeRe(pattern, options),
    braces: (pattern, options) => {
        if (options.expand !== true) throw new TypeError("Only brace expansion is supported by this private adapter");
        const opening = pattern.indexOf("{");
        if (opening < 0 || pattern.indexOf("}", opening) < 0) return [pattern];
        // Preserve the existing parser/input cap and default numeric-range guard.
        // Its parser is iterative; known-vulnerable compile/expand AST walkers
        // are not bundled or invoked. Walk this small tree iteratively too.
        const pending = [parseRangeTree(pattern)];
        while (pending.length) {
            const node = pending.pop();
            if (node.invalid || node.dollar) continue;
            if (node.nodes && node.ranges > 0) {
                const args = node.nodes.filter(part => part.type === "text").map(part => part.value);
                const integer = value => typeof value === "number" ? Number.isInteger(value) :
                    typeof value === "string" && value.trim() !== "" && Number.isInteger(Number(value));
                const step = args[2] === undefined ? 1 : args[2];
                const limit = args.length > 2 ? undefined : 1000;
                if (integer(args[0]) && integer(args[1]) &&
                    (Number(args[1]) - Number(args[0])) / Number(step) >= limit) {
                    throw new RangeError("expanded array length exceeds range limit. Use options.rangeLimit to increase or disable the limit.");
                }
                continue;
            }
            if (node.nodes) for (const child of node.nodes) pending.push(child);
        }
        const output = expandBraces(pattern);
        return options.nodupes === true ? [...new Set(output)] : output;
    }
};
