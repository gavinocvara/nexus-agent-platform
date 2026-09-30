import { expect, type Page, test } from "@playwright/test";

function watch(page: Page) {
  const errors: string[] = [];
  const writes: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("request", (request) => {
    if (!["GET", "HEAD"].includes(request.method())) writes.push(`${request.method()} ${request.url()}`);
  });
  return { errors, writes };
}

async function noHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, "page must not scroll horizontally").toBeLessThanOrEqual(0);
  for (const selector of [".nx-display", ".nx-topbar", ".nx-footer"]) {
    const box = await page.locator(selector).first().boundingBox();
    if (box) expect(box.x + box.width, `${selector} stays inside the viewport`).toBeLessThanOrEqual(page.viewportSize()!.width + 1);
  }
}

/** The WebGL canvas actually drew something: not a single flat colour. */
async function canvasHasContent(page: Page) {
  await page.waitForSelector(".nx-stage canvas");
  await page.waitForTimeout(3000);
  const spread = await page.evaluate(() => {
    const canvas = document.querySelector<HTMLCanvasElement>(".nx-stage canvas");
    if (!canvas) return 0;
    const probe = document.createElement("canvas");
    probe.width = 64;
    probe.height = 40;
    const context = probe.getContext("2d");
    if (!context) return 0;
    context.drawImage(canvas, 0, 0, 64, 40);
    const data = context.getImageData(0, 0, 64, 40).data;
    let min = 255;
    let max = 0;
    for (let i = 0; i < data.length; i += 4) {
      const luma = (data[i]! + data[i + 1]! + data[i + 2]!) / 3;
      min = Math.min(min, luma);
      max = Math.max(max, luma);
    }
    return max - min;
  });
  expect(spread, "scene renders visible structure").toBeGreaterThan(40);
}

test("spatial view renders, stays in bounds, and only reads", async ({ page }) => {
  const { errors, writes } = watch(page);
  await page.goto("/?capture=1");
  await expect(page.locator(".nx-menu__item")).toHaveCount(7);
  await canvasHasContent(page);
  await noHorizontalOverflow(page);
  await expect(page.locator(".nx-display")).toBeVisible();
  expect(errors).toEqual([]);
  expect(writes, "the Command Center never writes").toEqual([]);
});

test("keyboard and menu navigation move through the systems", async ({ page }) => {
  await page.goto("/?capture=1");
  await page.locator("body").press("ArrowDown");
  await page.locator("body").press("ArrowDown");
  await expect(page.locator(".nx-menu__title")).toHaveText("AEGISOPS");
  await expect(page.locator('.nx-menu__item[aria-current="true"]')).toHaveText(/AEGISOPS/);
  await page.getByRole("button", { name: /PATCHFORGE/ }).click();
  await expect(page.locator("#nx-display-title")).toHaveText("PatchForge");
  await page.locator("body").press("Escape");
  await expect(page.locator(".nx-menu__title")).toHaveText("NEXUS");
});

test("replay is unmistakably labelled and names scripted stand-ins", async ({ page }) => {
  const { writes } = watch(page);
  await page.goto("/?mode=replay&episode=owner_approval&t=24&paused=1&capture=1");
  await expect(page.locator(".nx-replay-band")).toContainText("NOT LIVE");
  await expect(page.locator(".nx-deck")).toContainText("scripted executor");
  await expect(page.locator(".nx-owner")).toContainText("SCRIPTED EVALUATION VERDICT");
  await expect(page.locator(".nx-owner")).toContainText("/nexus ship|revise|reject");
  await expect(page.locator(".nx-orb-label__scripted").first()).toBeVisible();
  await noHorizontalOverflow(page);
  expect(writes).toEqual([]);
});

test("data view is a complete text equivalent", async ({ page }) => {
  await page.goto("/?view=data&capture=1");
  const sheet = page.getByRole("region", { name: "Data view" });
  for (const name of ["NEXUS", "AegisOps", "PatchForge", "SentinelQA", "Resident Engineer", "Memory", "Engram"]) {
    await expect(sheet.getByRole("heading", { name: new RegExp(name) }).first()).toBeVisible();
  }
  await expect(sheet.getByRole("table").first()).toBeVisible();
});

test("without WebGL the dashboard falls back to the data view", async ({ page }) => {
  await page.goto("/?webgl=0");
  await expect(page.getByRole("region", { name: "Data view" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "SPACE" })).toBeDisabled();
  await expect(page.locator(".nx-stage canvas")).toHaveCount(0);
});

test("small screens keep everything reachable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?capture=1");
  await expect(page.locator(".nx-menu__item")).toHaveCount(7);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await page.getByRole("button", { name: "Next system" }).click();
  await page.getByRole("button", { name: "Next system" }).click();
  await expect(page.locator(".nx-menu__title")).toHaveText("AEGISOPS");
});
