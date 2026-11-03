#!/usr/bin/env node
"use strict";

// Transport compression only. The full source DOM captures and builder stay intact.
const fs = require("node:fs");
const path = require("node:path");
const childProcess = require("node:child_process");
const directory = __dirname;
const concepts = {
  A: { slug: "a-precision-lab", conceptName: "A · Precision Lab 精密仪器" },
  B: { slug: "b-control-room", conceptName: "B · Control Room 测量控制台" },
  C: { slug: "c-spatial-compass", conceptName: "C · Spatial Compass 方向可视化" },
};
const minimumMatch = 8;
const maximumCandidates = 128;

/** LZ77 tokens: literal strings or [backward distance, match length]. */
function pack(source) {
  const positions = new Map();
  const tokens = [];
  let literal = "";
  let offset = 0;
  function remember(index) {
    if (index + minimumMatch > source.length) return;
    const prefix = source.slice(index, index + minimumMatch);
    const bucket = positions.get(prefix);
    if (bucket) {
      bucket.push(index);
      if (bucket.length > maximumCandidates) bucket.shift();
    } else positions.set(prefix, [index]);
  }
  function flushLiteral() {
    if (literal) { tokens.push(literal); literal = ""; }
  }
  while (offset < source.length) {
    const candidates = positions.get(source.slice(offset, offset + minimumMatch)) || [];
    let bestLength = 0;
    let bestDistance = 0;
    for (let index = candidates.length - 1; index >= 0; index--) {
      const previous = candidates[index];
      let length = minimumMatch;
      while (offset + length < source.length && source[previous + length] === source[offset + length]) length++;
      if (length > bestLength) {
        bestLength = length;
        bestDistance = offset - previous;
        if (offset + length === source.length) break;
      }
    }
    const tokenCost = bestLength ? JSON.stringify([bestDistance, bestLength]).length + (literal ? 4 : 1) : Infinity;
    const literalCost = bestLength ? JSON.stringify(source.slice(offset, offset + bestLength)).length - 2 : 0;
    if (bestLength >= minimumMatch && tokenCost < literalCost) {
      flushLiteral();
      tokens.push([bestDistance, bestLength]);
      const end = offset + bestLength;
      while (offset < end) remember(offset++);
    } else {
      literal += source[offset];
      remember(offset++);
    }
  }
  flushLiteral();
  return tokens;
}

// This exact small decoder is embedded into the Figma script. It needs no globals,
// eval, modules or browser APIs, and its growing output supports overlapping matches.
function unpack(tokens) {
  const output = [];
  for (const token of tokens) {
    if (typeof token === "string") {
      for (let index = 0; index < token.length; index++) output.push(token[index]);
    } else {
      const distance = token[0];
      for (let index = 0; index < token[1]; index++) output.push(output[output.length - distance]);
    }
  }
  return output.join("");
}

function validateAsyncSyntax(code) {
  const result = childProcess.spawnSync(process.execPath, ["--check", "--input-type=module"], {
    input: "async function useFigmaScript() {\n" + code + "\n}\n",
    encoding: "utf8",
    maxBuffer: 1024 * 1024,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error("Invalid async Figma script syntax:\n" + result.stderr);
}

// Preserve newlines/ASI and literal contents. Only remove optional horizontal
// whitespace outside strings and regular expressions in this known builder.
function compactBuilder(source) {
  const input = source.replace(/^\/\*[\s\S]*?\*\/\s*/, "").split("\n")
    .filter(line => !/^\s*\/\//.test(line)).map(line => line.trimStart()).join("\n");
  let output = "";
  for (let index = 0; index < input.length; index++) {
    const character = input[index];
    const previous = output.trimEnd().slice(-1);
    if (character === '"' || character === "'") {
      const quote = character;
      output += character;
      while (++index < input.length) {
        output += input[index];
        if (input[index] === "\\") { output += input[++index]; continue; }
        if (input[index] === quote) break;
      }
    } else if (character === "/" && (!previous || /[([{},=:!?;&|+*%^~<>-]/.test(previous))) {
      output += character;
      let characterClass = false;
      while (++index < input.length) {
        const next = input[index];
        output += next;
        if (next === "\\") { output += input[++index]; continue; }
        if (next === "[") characterClass = true;
        if (next === "]") characterClass = false;
        if (next === "/" && !characterClass) break;
      }
    } else if (character === " " || character === "\t" || character === "\r") {
      let end = index;
      while (/[ \t\r]/.test(input[end + 1] || "\n")) end++;
      const next = input[end + 1] || "";
      if ((/[\w$]/.test(previous) && /[\w$]/.test(next)) || (previous === "+" && next === "+")
        || (previous === "-" && next === "-") || (/\d/.test(previous) && next === ".")) output += " ";
      index = end;
    } else output += character;
  }
  return output;
}

function prepare(concept, state, builder, rebuild) {
  const definition = concepts[concept];
  const readLayout = form => {
    const captured = JSON.parse(fs.readFileSync(path.join(directory, definition.slug + "-" + form + "-dom.json"), "utf8"));
    return { viewport: captured.viewport, background: captured.background, elements: captured.elements };
  };
  const config = {
    pageId: state.pages[concept],
    slug: definition.slug,
    conceptName: definition.conceptName,
    rebuild,
    desktop: readLayout("desktop"),
    mobile: readLayout("mobile"),
  };
  const source = JSON.stringify(config);
  const packed = pack(source);
  const decoded = unpack(packed);
  if (decoded !== source) throw new Error("LZ77 round-trip failed for concept " + concept);
  const packedSource = JSON.stringify(packed);
  const code = unpack.toString() + "\nconst PACKED = " + packedSource
    + ";\nconst CONFIG = JSON.parse(unpack(PACKED));\n" + builder;
  validateAsyncSyntax(code);
  return {
    code,
    metrics: {
      concept,
      conceptName: definition.conceptName,
      pageId: config.pageId,
      configChars: source.length,
      packedChars: packedSource.length,
      builderChars: builder.length,
      codeChars: code.length,
      withinLimit: code.length < 50000,
      remainingChars: 50000 - code.length,
      ratio: Number((packedSource.length / source.length).toFixed(4)),
      literalTokens: packed.filter(token => typeof token === "string").length,
      matchTokens: packed.filter(token => typeof token !== "string").length,
      roundTrip: true,
      asyncSyntax: true,
      omittedNonrenderingDiagnostics: true,
    },
  };
}

try {
  const selected = String(process.argv[2] || "").toUpperCase();
  if (!Object.hasOwn(concepts, selected)) throw new Error("Usage: node figma/prepare-payload.cjs A|B|C");
  const state = JSON.parse(fs.readFileSync(path.join(directory, "state.json"), "utf8"));
  const sourceBuilder = fs.readFileSync(path.join(directory, "build-from-layout.js"), "utf8");
  // This known builder uses no multiline string/template literals. Preserve every
  // newline for ASI and compact only indentation and complete line comments.
  const builder = compactBuilder(sourceBuilder);
  const prepared = {};
  for (const concept of Object.keys(concepts)) prepared[concept] = prepare(concept, state, builder, process.argv.includes("--rebuild"));
  const payload = prepared[selected];
  if (!payload.metrics.withinLimit) throw new Error("Figma payload exceeds the 50,000 character boundary: " + selected + " = " + payload.code.length);
  process.stdout.write(JSON.stringify({ state, code: payload.code,
    metrics: { ...payload.metrics, allConcepts: Object.fromEntries(Object.entries(prepared).map(([key, value]) => [key, value.metrics])) } }));
} catch (error) {
  process.stderr.write((error instanceof Error ? error.message : String(error)) + "\n");
  process.exitCode = 1;
}
