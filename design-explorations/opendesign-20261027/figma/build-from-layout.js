/* Inject `const CONFIG = { pageId, slug, conceptName, desktop, mobile };` above this file.
 * Plain JavaScript for use_figma: top-level await and return are intentional.
 * Scoped adaptations of the official createVariableCollection and
 * createComponentWithVariants helpers: page selection happens exactly once.
 */
const created = new Set();
const mutated = new Set();
const deleted = new Set();
const createdVariables = new Set();
const createdCollections = new Set();
const createdStyles = new Set();
const note = node => { created.add(node.id); return node; };
function noteTree(node) {
  note(node);
  if ("children" in node) for (const child of node.children) noteTree(child);
  return node;
}
const page = await figma.getNodeByIdAsync(CONFIG.pageId);
if (!page || page.type !== "PAGE") throw new Error("Configured page does not exist: " + CONFIG.pageId);
await figma.setCurrentPageAsync(page);
const namespace = "__OpenDesign / " + CONFIG.slug;
const screenNames = [CONFIG.conceptName + " / Desktop", CONFIG.conceptName + " / Mobile"];
const oldScreens = screenNames.map(name => page.children.find(node => node.name === name));
if (oldScreens.some(Boolean)) {
  if (!oldScreens.every(Boolean)) throw new Error("Partial screen pair exists. Inspect the exact existing frame before retrying.");
  if (!CONFIG.rebuild) return { status: "already-exists", pageId: page.id, createdNodeIds: [], mutatedNodeIds: [], deletedNodeIds: [],
    frames: oldScreens.map(node => ({ id: node.id, name: node.name, width: node.width, height: node.height })) };
}
const reusableRoots = new Map(oldScreens.filter(Boolean).map(node => [node.name, node]));
for (const data of [CONFIG.desktop, CONFIG.mobile]) {
  if (!data || !data.viewport || !Array.isArray(data.elements)) throw new Error("Invalid captured layout.");
  if (!(data.viewport.w > 0 && data.viewport.h > 0)) throw new Error("Invalid viewport dimensions.");
}
const elements = [CONFIG.desktop, CONFIG.mobile].flatMap(data => data.elements);
const available = await figma.listAvailableFontsAsync();
const fontFamilies = ["Instrument Sans", "Space Grotesk", "Sora", "IBM Plex Mono", "Noto Sans SC"];
function fontWeight(style) {
  const name = style.toLowerCase().replace(/[\s_-]/g, "");
  if (/black|heavy/.test(name)) return 900;
  if (/extrabold|ultrabold/.test(name)) return 800;
  if (/semibold|demibold/.test(name)) return 600;
  if (/bold/.test(name)) return 700;
  if (/medium/.test(name)) return 500;
  if (/extralight|ultralight/.test(name)) return 200;
  if (/thin/.test(name)) return 100;
  if (/light/.test(name)) return 300;
  return 400;
}
function fontFor(element, forceHan) {
  const source = String(element.fontFamily || "Instrument Sans").split(",")[0].replace(/["']/g, "").trim();
  const family = forceHan ? "Noto Sans SC" : fontFamilies.includes(source) ? source : /mono/i.test(source) ? "IBM Plex Mono" : "Instrument Sans";
  const candidates = available.map(font => font.fontName).filter(font => font.family === family && !/italic/i.test(font.style));
  if (!candidates.length) throw new Error("Required font family is unavailable: " + family);
  const weight = Number(element.fontWeight) || 400;
  return candidates.sort((a, b) => Math.abs(fontWeight(a.style) - weight) - Math.abs(fontWeight(b.style) - weight)
    || fontWeight(a.style) - fontWeight(b.style))[0];
}
// The SVG importer can preserve axis text. Preload every available source-family style.
const preload = available.map(font => font.fontName).filter(font => fontFamilies.includes(font.family));
await Promise.all(preload.map(font => figma.loadFontAsync(font)));
const usedFonts = new Set();
function color(value) {
  if (!value || value === "transparent" || value === "none") return null;
  const source = String(value).trim();
  let result;
  if (source[0] === "#") {
    let hex = source.slice(1);
    if (hex.length === 3 || hex.length === 4) hex = hex.split("").map(c => c + c).join("");
    if (hex.length !== 6 && hex.length !== 8) throw new Error("Unsupported CSS color: " + source);
    result = { r: parseInt(hex.slice(0, 2), 16) / 255, g: parseInt(hex.slice(2, 4), 16) / 255,
      b: parseInt(hex.slice(4, 6), 16) / 255, a: hex.length === 8 ? parseInt(hex.slice(6, 8), 16) / 255 : 1 };
  } else {
    const match = source.match(/^rgba?\(\s*([\d.]+)[, ]+([\d.]+)[, ]+([\d.]+)(?:\s*[,/]\s*([\d.]+))?\s*\)$/i);
    if (!match) throw new Error("Unsupported CSS color: " + source);
    result = { r: Number(match[1]) / 255, g: Number(match[2]) / 255, b: Number(match[3]) / 255,
      a: match[4] === undefined ? 1 : Number(match[4]) };
  }
  return result.a <= 0 ? null : result;
}
const rgbKey = value => [value.r, value.g, value.b].map(channel => Math.round(channel * 255).toString(16).padStart(2, "0")).join("");
const allCollections = await figma.variables.getLocalVariableCollectionsAsync();
const allVariables = await figma.variables.getLocalVariablesAsync();
async function createVariableCollection(name, modeNames) {
  const existing = allCollections.find(collection => collection.name === name);
  if (existing) return { collection: existing, modeIds: { [modeNames[0]]: existing.defaultModeId } };
  const collection = figma.variables.createVariableCollection(name);
  createdCollections.add(collection.id);
  collection.renameMode(collection.modes[0].modeId, modeNames[0]);
  return { collection, modeIds: { [modeNames[0]]: collection.modes[0].modeId } };
}
const foundation = await createVariableCollection(namespace + " / Theme", ["Value"]);
const collection = foundation.collection;
const modeId = foundation.modeIds.Value;
const variableMap = new Map(allVariables.filter(variable => variable.variableCollectionId === collection.id).map(variable => [variable.name, variable]));
function variable(name, type, value, scopes, primitive) {
  if (variableMap.has(name)) return variableMap.get(name);
  const result = figma.variables.createVariable(name, collection, type);
  result.scopes = scopes;
  result.hiddenFromPublishing = Boolean(primitive);
  result.setVariableCodeSyntax("WEB", "var(--od-" + CONFIG.slug + "-" + name.replace(/[^a-zA-Z0-9]+/g, "-").toLowerCase() + ")");
  result.setValueForMode(modeId, value);
  createdVariables.add(result.id);
  variableMap.set(name, result);
  return result;
}
const colorTokens = new Map();
function colorToken(css, role) {
  const rgba = color(css);
  if (!rgba) return null;
  const hex = rgbKey(rgba), key = role + "/" + hex;
  if (colorTokens.has(key)) return colorTokens.get(key);
  const primitive = variable("primitive/color/" + hex, "COLOR", { r: rgba.r, g: rgba.g, b: rgba.b }, [], true);
  const scopes = role === "text" ? ["TEXT_FILL"] : role === "border" ? ["STROKE_COLOR"] : ["FRAME_FILL", "SHAPE_FILL"];
  const semantic = variable("color/" + key, "COLOR", { type: "VARIABLE_ALIAS", id: primitive.id }, scopes, false);
  colorTokens.set(key, semantic);
  return semantic;
}
function numberToken(value, role, scope) {
  const key = String(Math.round(value * 10000) / 10000).replace(".", "_");
  const primitive = variable("primitive/number/" + key, "FLOAT", value, [], true);
  return variable(role + "/" + key, "FLOAT", { type: "VARIABLE_ALIAS", id: primitive.id }, [scope], false);
}
for (const data of [CONFIG.desktop, CONFIG.mobile]) colorToken(data.background, "surface");
for (const element of elements) {
  if (element.fill) colorToken(element.fill, "surface");
  if (element.color) colorToken(element.color, "text");
  if (element.strokeWidth > 0 && element.stroke) colorToken(element.stroke, "border");
  if (element.radius > 0) numberToken(element.radius, "radius", "CORNER_RADIUS");
}
const zeroGap = numberToken(0, "spacing", "GAP");
function paints(css, role) {
  const rgba = color(css);
  if (!rgba) return [];
  const paint = { type: "SOLID", color: { r: rgba.r, g: rgba.g, b: rgba.b }, opacity: rgba.a };
  return [figma.variables.setBoundVariableForPaint(paint, "color", colorToken(css, role))];
}
const localStyles = await figma.getLocalTextStylesAsync();
const textStyles = new Map(localStyles.map(style => [style.name, style]));
function textStyle(element) {
  const font = fontFor(element, false), size = Number(element.fontSize) || 12;
  const name = namespace + " / Type / " + font.family + " / " + size + " / " + font.style;
  if (textStyles.has(name)) return textStyles.get(name);
  const style = figma.createTextStyle();
  style.name = name;
  style.fontName = font;
  style.fontSize = size;
  style.lineHeight = { unit: "AUTO" };
  createdStyles.add(style.id);
  textStyles.set(name, style);
  return style;
}
// Finish the minimal shared text foundations before constructing dependent nodes.
for (const element of elements) if (element.type === "text" || element.type === "button") textStyle(element);
async function styleText(node, element, label) {
  const base = fontFor(element, false), han = fontFor(element, true);
  node.fontName = base;
  node.characters = String(element.text || "");
  await node.setTextStyleIdAsync(textStyle(element).id);
  node.fontName = base;
  node.fontSize = Number(element.fontSize) || 12;
  node.lineHeight = { unit: "PIXELS", value: Number(element.lineHeight) || node.fontSize * 1.4 };
  node.letterSpacing = { unit: "PIXELS", value: Number(element.letterSpacing) || 0 };
  node.fills = paints(element.color || "#000000", "text");
  node.opacity = element.opacity === undefined ? 1 : Math.max(0, Math.min(1, Number(element.opacity)));
  usedFonts.add(base.family);
  const text = node.characters;
  // Keep the product Latin face and use the actual CJK fallback for Han glyphs.
  for (let index = 0; index < text.length; index++) {
    if (/[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f]/.test(text[index])) {
      node.setRangeFontName(index, index + 1, han);
      usedFonts.add(han.family);
    }
  }
  node.textAlignHorizontal = label ? "CENTER" : element.textAlign === "right" ? "RIGHT" : element.textAlign === "center" ? "CENTER" : "LEFT";
  node.textAutoResize = "WIDTH_AND_HEIGHT";
  if (!label && !element.singleLine) {
    node.resize(Math.max(1, element.w + 1.25), Math.max(1, element.h));
    node.textAutoResize = "HEIGHT";
  }
}
function transparentFrame(name, width, height, parent) {
  const frame = note(figma.createFrame());
  frame.name = name;
  frame.fills = [];
  frame.clipsContent = false;
  frame.resize(Math.max(1, width), Math.max(1, height));
  parent.appendChild(frame);
  return frame;
}
const buttonElements = elements.filter(element => element.type === "button");
const signature = element => JSON.stringify([element.fill || null, element.stroke || null, element.strokeWidth || 0,
  element.radius || 0, element.fontFamily, element.fontSize, element.fontWeight, element.color, element.letterSpacing || 0]);
const buttonDefinitions = [...new Map(buttonElements.map(element => [signature(element), element])).entries()].sort((a, b) => a[0].localeCompare(b[0]));
const buttonKinds = new Map(buttonDefinitions.map(([key], index) => [key, String(index + 1).padStart(2, "0")]));
async function createComponentWithVariants(config) {
  const existing = page.findOne(node => node.type === "COMPONENT_SET" && node.name === config.name);
  if (existing) {
    if (existing.children.length !== buttonDefinitions.length) throw new Error("Existing button family does not match this capture.");
    return { componentSet: existing, variants: existing.children };
  }
  const components = [];
  for (let index = 0; index < buttonDefinitions.length; index++) {
    const element = buttonDefinitions[index][1];
    const component = note(figma.createComponent());
    component.name = "Kind=" + String(index + 1).padStart(2, "0");
    component.layoutMode = "HORIZONTAL";
    component.primaryAxisSizingMode = "FIXED";
    component.counterAxisSizingMode = "FIXED";
    component.primaryAxisAlignItems = "CENTER";
    component.counterAxisAlignItems = "CENTER";
    component.paddingTop = component.paddingBottom = component.paddingLeft = component.paddingRight = 0;
    component.itemSpacing = 0;
    component.setBoundVariable("itemSpacing", zeroGap);
    for (const field of ["paddingTop", "paddingBottom", "paddingLeft", "paddingRight"]) component.setBoundVariable(field, zeroGap);
    component.resize(Math.max(1, element.w), Math.max(1, element.h));
    component.fills = paints(element.fill, "surface");
    component.strokes = element.strokeWidth > 0 ? paints(element.stroke, "border") : [];
    if (component.strokes.length) { component.strokeWeight = element.strokeWidth; component.strokeAlign = "INSIDE"; }
    component.cornerRadius = Math.max(0, element.radius || 0);
    if (element.radius > 0) component.setBoundVariable("cornerRadius", numberToken(element.radius, "radius", "CORNER_RADIUS"));
    page.appendChild(component);
    const label = note(figma.createText());
    label.name = "Label";
    await styleText(label, element, true);
    component.appendChild(label);
    await fitButtonLabel(label, element);
    components.push(component);
  }
  const componentSet = note(figma.combineAsVariants(components, page));
  componentSet.name = config.name;
  componentSet.description = "Editable controls for " + CONFIG.conceptName + ". Use Label for text; fixed captured sizes preserve the scientific instrument panel layout.";
  const labelProperty = componentSet.addComponentProperty("Label", "TEXT", "Control");
  for (const component of components) component.children[0].componentPropertyReferences = { characters: labelProperty };
  const gap = 24, columnWidth = 220, rowHeight = 84, columns = 3;
  components.forEach((component, index) => { component.x = gap + index % columns * columnWidth; component.y = gap + Math.floor(index / columns) * rowHeight; });
  componentSet.resize(columns * columnWidth + gap * 2, Math.ceil(components.length / columns) * rowHeight + gap * 2);
  componentSet.x = 2200; componentSet.y = 80;
  return { componentSet, variants: components };
}
async function fitButtonLabel(label, element) {
  // Measure actual Figma glyph widths, then give every label one native centered line.
  label.textAutoResize = "WIDTH_AND_HEIGHT";
  const maximumWidth = Math.max(1, element.w - (element.w < 60 ? 0 : 6));
  const naturalWidth = label.width;
  if (naturalWidth > maximumWidth) {
    const size = Math.max(6, (Number(element.fontSize) || 12) * maximumWidth / naturalWidth);
    label.fontSize = size;
    label.lineHeight = { unit: "PIXELS", value: size * 1.4 };
  }
  label.textAutoResize = "WIDTH_AND_HEIGHT";
  label.textAlignHorizontal = "CENTER";
}
function noteDeletedTree(node) {
  deleted.add(node.id);
  if ("children" in node) for (const child of node.children) noteDeletedTree(child);
}
if (CONFIG.rebuild) {
  for (const root of reusableRoots.values()) {
    for (const child of [...root.children]) { noteDeletedTree(child); child.remove(); }
    mutated.add(root.id);
  }
  const previousFamilies = page.children.filter(node => node.type === "COMPONENT_SET" && node.name === namespace + " / Button");
  for (const family of previousFamilies) { noteDeletedTree(family); family.remove(); }
  if (reusableRoots.size || previousFamilies.length) mutated.add(page.id);
}
const buttons = await createComponentWithVariants({ name: namespace + " / Button" });
const variantMap = new Map(buttons.variants.map(component => [component.name.split("=")[1], component]));
function groupBounds(items) {
  const x = Math.min(...items.map(item => item.x)), y = Math.min(...items.map(item => item.y));
  return { x, y, w: Math.max(...items.map(item => item.x + item.w)) - x,
    h: Math.max(...items.map(item => item.y + item.h)) - y };
}
function groupContainer(layer, group, items) {
  const box = groupBounds(items);
  const node = transparentFrame(group || "Content", box.w, box.h, layer);
  node.x = box.x; node.y = box.y;
  return { node, box };
}
function chartSvg(svg, width, height) {
  let result = svg.replace(/font-family="([^"]*)"/g, (match, family) => {
    const decoded = family.replace(/&quot;/g, "").split(",")[0].trim();
    return 'font-family="' + (fontFamilies.includes(decoded) ? decoded : "IBM Plex Mono") + '"';
  });
  const viewBox = result.match(/viewBox="([^"]+)"/);
  if (viewBox) {
    const values = viewBox[1].trim().split(/[\s,]+/).map(Number);
    if (values.length === 4 && values[2] > 0 && values[3] > 0) {
      result = result.replace(/viewBox="[^"]+"/, 'viewBox="0 0 ' + width + " " + height + '"');
      result = result.replace(/(<svg\b[^>]*>)([\s\S]*)(<\/svg>\s*)$/, (match, head, body, tail) =>
        head + '<g transform="scale(' + width / values[2] + " " + height / values[3] + ") translate(" + -values[0] + " " + -values[1] + ')">' + body + "</g>" + tail);
    }
  }
  return result;
}
async function buildScreen(data, name, canvasX) {
  const root = reusableRoots.get(name) || note(figma.createFrame());
  if (reusableRoots.has(name)) mutated.add(root.id);
  root.name = name; root.resize(data.viewport.w, data.viewport.h);
  root.x = canvasX; root.y = 80;
  root.fills = paints(data.background, "surface");
  root.clipsContent = true;
  page.appendChild(root); mutated.add(page.id);
  const backgroundLayer = transparentFrame("01 / Surfaces and dividers", root.width, root.height, root);
  const vectorLayer = transparentFrame("02 / Scientific chart vectors", root.width, root.height, root);
  const contentLayer = transparentFrame("03 / Native labels and controls", root.width, root.height, root);
  const groups = new Map();
  for (const element of data.elements) {
    if (element.group === "feedback" || !(element.w > 0 && element.h > 0)) continue;
    const key = element.group || "Content";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(element);
  }
  for (const [group, items] of groups) {
    const shapes = items.filter(element => element.type === "rect" || element.type === "ellipse");
    if (shapes.length) {
      const container = groupContainer(backgroundLayer, group, shapes);
      for (const element of shapes) {
        const isCircle = element.type === "ellipse" && Math.abs(element.w - element.h) < 0.5;
        const node = note(isCircle ? figma.createEllipse() : figma.createRectangle());
        node.name = (element.tag || "Surface") + " / " + element.id;
        node.resize(element.w, element.h);
        node.fills = paints(element.fill, "surface");
        node.strokes = element.strokeWidth > 0 ? paints(element.stroke, "border") : [];
        if (node.strokes.length) { node.strokeWeight = element.strokeWidth; node.strokeAlign = "INSIDE"; }
        if (!isCircle && element.radius > 0) {
          node.cornerRadius = Math.min(element.radius, element.w / 2, element.h / 2);
          // Oversized CSS pill radii are constrained by the captured geometry.
          if (node.cornerRadius === element.radius) node.setBoundVariable("cornerRadius", numberToken(element.radius, "radius", "CORNER_RADIUS"));
        }
        container.node.appendChild(node); node.x = element.x - container.box.x; node.y = element.y - container.box.y;
      }
    }
    for (const element of items.filter(item => item.type === "svg")) {
      const node = noteTree(figma.createNodeFromSvg(chartSvg(element.svg, element.w, element.h)));
      node.name = group + " / Editable chart";
      vectorLayer.appendChild(node); node.x = element.x; node.y = element.y;
      node.resize(element.w, element.h);
    }
    const content = items.filter(item => item.type === "text" || item.type === "button");
    if (!content.length) continue;
    const container = groupContainer(contentLayer, group, content);
    const remaining = content.filter(item => item.type === "text");
    const consumed = new Set();
    for (const element of remaining) {
      if (consumed.has(element.id)) continue;
      const peers = remaining.filter(item => !consumed.has(item.id) && Math.abs(item.y - element.y) < 0.25
        && Math.abs(item.h - element.h) < 0.25 && item.fontSize === element.fontSize && item.lineHeight === element.lineHeight).sort((a, b) => a.x - b.x);
      const gaps = peers.slice(1).map((item, index) => item.x - peers[index].x - peers[index].w - 1.25);
      const rowEligible = !element.singleLine && peers.length > 1 && gaps.every(gap => gap >= 0) && Math.max(...gaps) - Math.min(...gaps) < 0.5;
      if (rowEligible) {
        const row = note(figma.createAutoLayout("HORIZONTAL"));
        row.name = "Label row / " + peers[0].text.slice(0, 30); row.fills = []; row.clipsContent = false;
        row.primaryAxisSizingMode = "AUTO"; row.counterAxisSizingMode = "AUTO";
        row.counterAxisAlignItems = "MIN"; row.itemSpacing = gaps.reduce((sum, gap) => sum + gap, 0) / gaps.length;
        container.node.appendChild(row); row.x = peers[0].x - container.box.x; row.y = peers[0].y - container.box.y;
        for (const peer of peers) {
          const text = note(figma.createText()); text.name = peer.text.slice(0, 60);
          await styleText(text, peer, false); row.appendChild(text); consumed.add(peer.id);
        }
      } else {
        const text = note(figma.createText()); text.name = element.text.slice(0, 60);
        await styleText(text, element, false); container.node.appendChild(text);
        text.x = element.x - container.box.x; text.y = element.y - container.box.y; consumed.add(element.id);
      }
    }
    for (const element of content.filter(item => item.type === "button")) {
      const master = variantMap.get(buttonKinds.get(signature(element)));
      if (!master) throw new Error("Missing deterministic button style.");
      const instance = noteTree(master.createInstance());
      instance.name = "Control / " + element.text;
      container.node.appendChild(instance); instance.resize(element.w, element.h);
      instance.x = element.x - container.box.x; instance.y = element.y - container.box.y;
      const property = Object.keys(instance.componentProperties).find(key => key.split("#")[0] === "Label");
      if (!property) throw new Error("Button Label property is missing.");
      instance.setProperties({ [property]: element.text });
      const label = instance.findOne(node => node.type === "TEXT" && node.name === "Label");
      if (!label) throw new Error("Button label text is missing.");
      await styleText(label, element, true); await fitButtonLabel(label, element);
    }
  }
  const descendants = root.findAll(() => true);
  const counts = {};
  for (const node of descendants) counts[node.type] = (counts[node.type] || 0) + 1;
  const imageFilledNodeIds = descendants.filter(node => "fills" in node && Array.isArray(node.fills) && node.fills.some(paint => paint.type === "IMAGE")).map(node => node.id);
  return { id: root.id, name: root.name, x: root.x, y: root.y, width: root.width, height: root.height,
    counts, capturedElements: data.elements.length, nativeTextCount: counts.TEXT || 0,
    buttonInstances: counts.INSTANCE || 0, imageFilledNodeIds };
}
const desktop = await buildScreen(CONFIG.desktop, screenNames[0], 80);
const mobile = await buildScreen(CONFIG.mobile, screenNames[1], 1600);
return { status: CONFIG.rebuild && reusableRoots.size ? "rebuilt" : "created", pageId: page.id, slug: CONFIG.slug, frames: [desktop, mobile],
  componentSetId: buttons.componentSet.id, componentIds: buttons.variants.map(node => node.id),
  createdNodeIds: [...created], mutatedNodeIds: [...mutated], deletedNodeIds: [...deleted],
  createdVariableCollectionIds: [...createdCollections], createdVariableIds: [...createdVariables], createdTextStyleIds: [...createdStyles],
  reusedVariableCollectionId: createdCollections.has(collection.id) ? null : collection.id,
  textFamilies: [...usedFonts].sort(), imageFilledNodes: desktop.imageFilledNodeIds.length + mobile.imageFilledNodeIds.length,
  counts: { createdNodes: created.size, variables: createdVariables.size, textStyles: createdStyles.size, buttonVariants: buttons.variants.length } };
