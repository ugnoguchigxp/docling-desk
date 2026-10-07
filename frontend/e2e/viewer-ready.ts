import { expect, type Page } from "@playwright/test";

export async function waitForViewerSurface(page: Page) {
  await expect(page.locator("#filename")).not.toBeEmpty();
  const source = page.locator("#original:visible,#slideCanvas iframe:visible");
  if (await source.count()) {
    await expect(
      page
        .frameLocator("#original:visible,#slideCanvas iframe:visible")
        .locator("body"),
    ).not.toBeEmpty();
    const handle = await source.elementHandle();
    const frame = await handle?.contentFrame();
    await frame?.waitForLoadState("load");
  }
  // DOM readiness precedes the compositor's iframe surface update in Chrome.
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
}
