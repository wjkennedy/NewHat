(function(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.NewHatThreadBlocks = api;
})(typeof globalThis === "object" ? globalThis : this, function() {
  function normalizedColor(value) {
    return typeof value === "string" ? value.toLowerCase() : "";
  }

  function planThreadBlocks(plan) {
    const palette = plan?.palette || [];
    const paletteIndexes = new Map(
      palette.map((color, index) => [normalizedColor(color), index]),
    );
    const regionIndexes = new Map();
    for (const region of plan?.regions || []) {
      const regionId = region.id || region.region_id;
      const paletteIndex = paletteIndexes.get(normalizedColor(region.color));
      if (regionId && paletteIndex !== undefined) {
        regionIndexes.set(regionId, paletteIndex);
      }
    }

    const blocks = [{ operations: [] }];
    for (const operation of plan?.operations || []) {
      if (operation.kind === "color") {
        if (blocks[blocks.length - 1].operations.length) {
          blocks.push({ operations: [] });
        }
        continue;
      }
      blocks[blocks.length - 1].operations.push(operation);
    }

    return blocks.filter(block => block.operations.length).map((block, blockIndex) => {
      let paletteIndex;
      for (const operation of block.operations) {
        if (operation.palette_index !== undefined) {
          paletteIndex = Number(operation.palette_index);
          break;
        }
        const operationIndex = paletteIndexes.get(normalizedColor(operation.color));
        if (operationIndex !== undefined) {
          paletteIndex = operationIndex;
          break;
        }
        const regionIndex = regionIndexes.get(operation.region_id);
        if (regionIndex !== undefined) {
          paletteIndex = regionIndex;
          break;
        }
      }
      if (paletteIndex === undefined) {
        paletteIndex = palette.length ? blockIndex % palette.length : 0;
      }
      return {
        paletteIndex,
        color: palette[paletteIndex] || palette[0] || "",
        operations: block.operations,
      };
    });
  }

  function selectedPlanOperations(plan, enabled = []) {
    const output = [];
    let previousPaletteIndex = null;
    for (const block of planThreadBlocks(plan)) {
      if (enabled[block.paletteIndex] === false) continue;
      if (
        output.length &&
        previousPaletteIndex !== block.paletteIndex
      ) {
        output.push({
          kind: "color",
          role: "color_change",
          color: block.color,
          palette_index: block.paletteIndex,
        });
      }
      for (const operation of block.operations) {
        output.push({
          ...operation,
          color: block.color,
          palette_index: block.paletteIndex,
        });
      }
      previousPaletteIndex = block.paletteIndex;
    }
    return output;
  }

  function operationBlockColors(operations, fallbackColors = []) {
    const colors = [];
    let blockIndex = 0;
    for (const operation of operations || []) {
      if (operation.kind === "color") {
        blockIndex += 1;
        if (operation.color) colors[blockIndex] = operation.color;
        continue;
      }
      if (!colors[blockIndex] && operation.color) {
        colors[blockIndex] = operation.color;
      }
    }
    const count = Math.max(
      1,
      (operations || []).filter(operation => operation.kind === "color").length + 1,
    );
    for (let index = 0; index < count; index += 1) {
      if (!colors[index]) {
        colors[index] =
          fallbackColors[Math.min(index, fallbackColors.length - 1)] || "";
      }
    }
    return colors;
  }

  function colorizeOperations(operations, blockColors, fallbackColors = []) {
    let blockIndex = 0;
    for (const operation of operations || []) {
      if (operation.kind === "color") blockIndex += 1;
      operation.color =
        blockColors[blockIndex] ||
        fallbackColors[Math.min(blockIndex, fallbackColors.length - 1)] ||
        "";
    }
    return operations;
  }

  return {
    colorizeOperations,
    operationBlockColors,
    planThreadBlocks,
    selectedPlanOperations,
  };
});
