const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");
const root = path.resolve(__dirname, "..");
const slugs = ["a-precision-lab", "b-control-room", "c-spatial-compass"];

async function captureLayout(page) {
  return page.evaluate(() => {
    const result = [];
    const bounds = rect => ({ x: rect.x, y: rect.y + scrollY, w: rect.width, h: rect.height });
    const visible = element => {
      const closedDetails = element.closest("details:not([open])");
      if (closedDetails && element !== closedDetails && !element.closest("summary")) return false;
      const box = element.getBoundingClientRect(), style = getComputedStyle(element);
      return box.width > 0 && box.height > 0 && style.visibility !== "hidden" && style.display !== "none" && Number(style.opacity) > 0;
    };
    const transparent = color => !color || color === "transparent" || color === "rgba(0, 0, 0, 0)";
    const groupFor = element => {
      const parent = element.closest("header,section,article,aside,footer,details,nav");
      return parent ? (parent.getAttribute("aria-label") || parent.className || parent.tagName).toString() : "Content";
    };
    let number = 0;
    const vector = (element, box, markup, name) => result.push({
      id: `${name}-${number++}`, type: "svg", ...box, group: groupFor(element),
      svg: `<svg xmlns="http://www.w3.org/2000/svg" width="${box.w}" height="${box.h}" viewBox="0 0 ${box.w} ${box.h}">${markup}</svg>`,
    });
    const pseudoBackground = (element, pseudoName) => {
      const pseudo = getComputedStyle(element, pseudoName), parent = getComputedStyle(element);
      if (pseudo.content === "none" || pseudo.display === "none" || transparent(pseudo.backgroundColor)) return;
      const box = bounds(element.getBoundingClientRect());
      const w = parseFloat(pseudo.width), h = parseFloat(pseudo.height);
      if (!Number.isFinite(w) || !Number.isFinite(h)) return;
      result.push({ id: `pseudo-background-${number++}`, type: "rect", w, h,
        x: box.x + parseFloat(parent.borderLeftWidth) + parseFloat(pseudo.left),
        y: box.y + parseFloat(parent.borderTopWidth) + parseFloat(pseudo.top),
        fill: pseudo.backgroundColor, radius: parseFloat(pseudo.borderTopLeftRadius) || 0, group: groupFor(element) });
    };
    for (const element of document.querySelectorAll("body *")) {
      if (!visible(element) || element.closest("svg") || ["SCRIPT", "STYLE", "LINK", "META"].includes(element.tagName)) continue;
      const style = getComputedStyle(element), box = bounds(element.getBoundingClientRect());
      const fill = style.backgroundColor;
      const radius = (parseFloat(style.borderTopLeftRadius) || 0) * (style.borderTopLeftRadius.includes("%") ? Math.min(box.w, box.h) / 100 : 1);
      const shape = Math.abs(box.w - box.h) < .5 && radius >= Math.min(box.w, box.h) / 2 ? "ellipse" : "rect";
      if (element.matches('input[type="checkbox"]')) {
        const native = style.appearance !== "none", accent = native ? style.accentColor : style.color;
        result.push({ id: `checkbox-${number++}`, type: "rect", ...box, radius: native ? 2 : radius,
          fill: element.checked && (native || !transparent(fill)) ? accent : null,
          stroke: accent, strokeWidth: 1, group: groupFor(element) });
        if (element.checked && !native && getComputedStyle(element, "::after").backgroundColor === accent) {
          const pseudo = getComputedStyle(element, "::after"), w = parseFloat(pseudo.width), h = parseFloat(pseudo.height);
          result.push({ id: `checkbox-mark-${number++}`, type: "rect", x: box.x + (box.w - w) / 2, y: box.y + (box.h - h) / 2, w, h, fill: accent, group: groupFor(element) });
        } else if (element.checked) {
          vector(element, box, `<path d="M ${box.w * .22} ${box.h * .52} L ${box.w * .43} ${box.h * .73} L ${box.w * .8} ${box.h * .3}" fill="none" stroke="white" stroke-width="${Math.max(1, box.w * .12)}" stroke-linecap="round" stroke-linejoin="round"/>`, "checkbox-mark");
        }
        continue;
      }
      if (element.matches(".beam.upper")) {
        const thickness = parseFloat(style.borderTopWidth), dash = thickness * 3, gap = thickness * 3;
        for (let offset = 0; offset < box.h; offset += dash + gap) result.push({ id: `upper-beam-dash-${number++}`, type: "rect", x: box.x, y: box.y + offset, w: box.w, h: Math.min(dash, box.h - offset), fill: style.borderTopColor, opacity: Number(style.opacity), group: groupFor(element) });
        vector(element, { x: box.x + box.w / 2 - 5, y: box.y - 4, w: 10, h: 8 }, `<path d="M 1 6 L 5 1 L 9 6" fill="none" stroke="${style.color}" stroke-width="2" opacity="${style.opacity}"/>`, "upper-arrow");
        continue;
      }
      if (element.matches(".chevron") && style.transform !== "none" && parseFloat(style.borderRightWidth) > 0) {
        const d = element.closest(".diagnostics-title") ? `M ${box.w * .3} 1 L ${box.w * .7} ${box.h / 2} L ${box.w * .3} ${box.h - 1}` : `M 1 ${box.h * .3} L ${box.w / 2} ${box.h * .7} L ${box.w - 1} ${box.h * .3}`;
        vector(element, box, `<path d="${d}" fill="none" stroke="${style.borderRightColor}" stroke-width="1"/>`, "diagnostic-chevron");
        continue;
      }
      if (["BUTTON", "SELECT"].includes(element.tagName)) {
        const label = element.tagName === "SELECT" ? element.selectedOptions[0].textContent + " ▾" : element.textContent.trim();
        result.push({ id: `control-${number++}`, type: "button", ...box, fill: transparent(fill) ? null : fill,
          stroke: style.borderTopColor, strokeWidth: parseFloat(style.borderTopWidth) || 0, radius, text: label,
          fontFamily: style.fontFamily.split(",")[0].replaceAll('"', "").trim(), fontSize: parseFloat(style.fontSize),
          fontWeight: Number(style.fontWeight) || 400, letterSpacing: parseFloat(style.letterSpacing) || 0, color: style.color, group: groupFor(element) });
        continue;
      }
      if (!transparent(fill) && element.tagName !== "BODY") result.push({
        id: `background-${number++}`, type: shape,
        ...box, fill, radius, group: groupFor(element), tag: element.tagName,
      });
      const sides = ["Top", "Right", "Bottom", "Left"];
      const uniformBorder = radius > 0 && parseFloat(style.borderTopWidth) > 0 && sides.every(side => style[`border${side}Width`] === style.borderTopWidth && style[`border${side}Color`] === style.borderTopColor && style[`border${side}Style`] === "solid");
      if (uniformBorder && !transparent(style.borderTopColor)) result.push({
        id: `rounded-border-${number++}`, type: shape, ...box,
        fill: null, stroke: style.borderTopColor, strokeWidth: parseFloat(style.borderTopWidth), strokeAlign: "INSIDE", radius, group: groupFor(element), tag: element.tagName,
      });
      for (const side of uniformBorder ? [] : sides) {
        const thickness = parseFloat(style[`border${side}Width`]) || 0;
        if (!thickness || style[`border${side}Style`] === "none" || transparent(style[`border${side}Color`])) continue;
        const coordinates = side === "Top" ? [box.x, box.y, box.w, thickness]
          : side === "Bottom" ? [box.x, box.y + box.h - thickness, box.w, thickness]
          : side === "Left" ? [box.x, box.y, thickness, box.h] : [box.x + box.w - thickness, box.y, thickness, box.h];
        result.push({ id: `border-${number++}`, type: "rect", x: coordinates[0], y: coordinates[1], w: coordinates[2], h: coordinates[3], fill: style[`border${side}Color`], group: groupFor(element) });
      }
      if (element.matches(".mark")) for (const pseudo of ["::before", "::after"]) pseudoBackground(element, pseudo);
      if (element.matches(".usb-status .port")) pseudoBackground(element, "::after");
      if (element.matches(".beam.tf,.beam.front")) vector(element, { x: box.x + box.w - 5, y: box.y - 4, w: 8, h: 10 }, `<path d="M 1 1 L 6 5 L 1 9" fill="none" stroke="${style.color}" stroke-width="2"/>`, "beam-arrow");
      if (element.matches(".sensor-origin")) {
        const pseudo = getComputedStyle(element, "::before"), border = parseFloat(pseudo.borderTopWidth);
        const w = parseFloat(pseudo.width) + (pseudo.boxSizing === "border-box" ? 0 : border * 2), h = parseFloat(pseudo.height) + (pseudo.boxSizing === "border-box" ? 0 : border * 2);
        result.push({ id: `origin-ring-${number++}`, type: "ellipse", x: box.x + (box.w - w) / 2, y: box.y + (box.h - h) / 2, w, h, fill: null, stroke: pseudo.borderTopColor, strokeWidth: border, strokeAlign: "INSIDE", group: groupFor(element) });
      }
      if (element.matches(".usb-status")) {
        const pseudo = getComputedStyle(element, "::after"), content = pseudo.content.replace(/^"|"$/g, "");
        if (content === "演示数据") {
          const context = document.createElement("canvas").getContext("2d");
          context.font = `${pseudo.fontWeight} ${pseudo.fontSize} ${pseudo.fontFamily}`;
          const textWidth = context.measureText(content).width, px = parseFloat(pseudo.paddingLeft), py = parseFloat(pseudo.paddingTop);
          const h = parseFloat(pseudo.lineHeight) + py * 2, w = textWidth + px * 2, x = box.x + box.w - w, y = box.y + (box.h - h) / 2;
          result.push({ id: `demo-badge-${number++}`, type: "rect", x, y, w, h, fill: pseudo.backgroundColor, radius: h / 2, group: groupFor(element) });
          result.push({ id: `demo-badge-text-${number++}`, type: "text", x: x + px, y: y + py, w: textWidth, h: parseFloat(pseudo.lineHeight), text: content, color: pseudo.color, fontFamily: pseudo.fontFamily.split(",")[0].replaceAll('"', "").trim(), fontSize: parseFloat(pseudo.fontSize), fontWeight: Number(pseudo.fontWeight) || 400, letterSpacing: parseFloat(pseudo.letterSpacing) || 0, lineHeight: parseFloat(pseudo.lineHeight), lineCount: 1, singleLine: true, textAlign: "left", group: groupFor(element) });
        }
      }
    }
    for (const svg of document.querySelectorAll("svg")) {
      if (!visible(svg)) continue;
      const clone = svg.cloneNode(true), sourceNodes = [svg, ...svg.querySelectorAll("*")], cloneNodes = [clone, ...clone.querySelectorAll("*")];
      sourceNodes.forEach((source, index) => {
        const style = getComputedStyle(source);
        if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) { cloneNodes[index].remove(); return; }
        for (const property of ["fill", "stroke", "stroke-width", "stroke-dasharray", "stroke-linecap", "stroke-linejoin", "opacity", "font-family", "font-size", "font-weight", "visibility"]) {
          cloneNodes[index].setAttribute(property, style.getPropertyValue(property));
        }
        cloneNodes[index].removeAttribute("class");
      });
      const box = bounds(svg.getBoundingClientRect());
      clone.setAttribute("width", box.w); clone.setAttribute("height", box.h); clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      result.push({ id: `svg-${number++}`, type: "svg", ...box, svg: clone.outerHTML, group: groupFor(svg) });
    }
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode, element = node.parentElement, text = node.textContent.trim();
      if (!text || !element || !visible(element) || element.closest("svg,script,style,button,select")) continue;
      const style = getComputedStyle(element);
      const raw = node.textContent, range = document.createRange(), fragments = [];
      let offset = 0;
      for (const character of raw) {
        const end = offset + character.length;
        range.setStart(node, offset); range.setEnd(node, end);
        const rect = [...range.getClientRects()].find(item => item.width > .05 && item.height > .05);
        if (rect && (character.trim() || fragments.length)) {
          const current = fragments.at(-1), overlap = current && Math.min(current.bottom, rect.bottom) - Math.max(current.top, rect.top);
          if (current && overlap > Math.min(current.bottom - current.top, rect.height) / 2) current.end = end;
          else if (character.trim()) fragments.push({ start: offset, end, top: rect.top, bottom: rect.bottom });
        }
        offset = end;
      }
      for (const fragment of fragments) {
        let start = fragment.start, end = fragment.end;
        while (start < end && !raw[start].trim()) start++;
        while (end > start && !raw[end - 1].trim()) end--;
        if (start === end) continue;
        range.setStart(node, start); range.setEnd(node, end);
        const box = bounds(range.getBoundingClientRect());
        const fragmentText = style.whiteSpace.startsWith("pre") ? raw.slice(start, end) : raw.slice(start, end).replace(/\s+/g, " ");
        result.push({ id: `text-${number++}`, type: "text", ...box, text: fragmentText, color: style.color,
          fontFamily: style.fontFamily.split(",")[0].replaceAll('"', "").trim(), fontSize: parseFloat(style.fontSize),
          fontWeight: Number(style.fontWeight) || 400, letterSpacing: parseFloat(style.letterSpacing) || 0,
          lineHeight: parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.4, lineCount: fragments.length, singleLine: true,
          textAlign: style.textAlign, group: groupFor(element), tag: element.tagName,
          button: Boolean(element.closest("button")), opacity: Number(style.opacity),
        });
      }
    }
    const bodyStyle = getComputedStyle(document.body);
    return { viewport: { w: innerWidth, h: Math.max(innerHeight, document.documentElement.scrollHeight) },
      background: bodyStyle.backgroundColor, elements: result,
      diagnostics: { title: document.title, scrollWidth: document.documentElement.scrollWidth, viewportWidth: innerWidth,
        buttons: [...document.querySelectorAll("button,summary,select")].filter(visible).map(element => ({ text: element.textContent.trim(), ...bounds(element.getBoundingClientRect()) })),
        text: document.body.innerText } };
  });
}

async function main() {
  const browser = await chromium.launch({ headless: true, channel: "chrome" });
  const results = [];
  try {
    for (const slug of slugs) {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
      const errors = []; page.on("pageerror", error => errors.push(error.message));
      const url = `http://127.0.0.1:8289/opendesign/mockups/${slug}/index.html`;
      const response = await page.goto(url); if (!response.ok()) throw new Error(`Failed ${url}`);
      await page.evaluate(() => document.fonts.ready);
      const desktop = await captureLayout(page);
      await fs.writeFile(path.join(root, "figma", `${slug}-desktop-dom.json`), JSON.stringify(desktop));
      await page.screenshot({ path: path.join(root, "previews", `${slug}-desktop.png`), fullPage: true });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.reload();
      await page.evaluate(() => document.fonts.ready);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const mobile = await captureLayout(page);
      await fs.writeFile(path.join(root, "figma", `${slug}-mobile-dom.json`), JSON.stringify(mobile));
      await page.screenshot({ path: path.join(root, "previews", `${slug}-mobile.png`), fullPage: true });
      results.push({ slug, errors, desktop: desktop.diagnostics, mobile: mobile.diagnostics });
      await page.close();
    }
    await fs.writeFile(path.join(root, "previews", "browser-checks.json"), JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results.map(result => ({ slug: result.slug, errors: result.errors,
      desktopOverflow: result.desktop.scrollWidth > result.desktop.viewportWidth, mobileOverflow: result.mobile.scrollWidth > result.mobile.viewportWidth })), null, 2));
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
