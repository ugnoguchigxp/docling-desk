import { expect, test } from "@playwright/test";

// Saved documents are not part of the synthetic lane. They are not redistributable
// from this repository. Obtain them on a machine that already has the private
// cache from `pnpm visual:prepare`, then run `pnpm test:e2e:saved` with
// UI_SAVED_DOCUMENTS=1 and without UI_SYNTHETIC. Record that result separately.
const slides = "cca5aede53b04a63b4f165b1c72794b2";
const sheets = "1357fa0e4b694bc7b06095c269cef984";

test.beforeEach(() => {
  test.skip(
    process.env.UI_SAVED_DOCUMENTS !== "1",
    "保存済み実資料は合成レーンに含めません。UI_SAVED_DOCUMENTS=1 と専用キャッシュが必要です。",
  );
});

for (const [kind, id] of [
  ["saved-slides", slides],
  ["saved-sheets", sheets],
] as const) {
  test(`saved ${kind} opens from the private cache`, async ({ page }) => {
    await page.goto(`/?job=${id}`);
    await expect(page.locator("#filename")).not.toBeEmpty();
    await page.locator("#saveMenu summary").click();
    await expect(page.locator("#saveMenu")).toHaveAttribute("open", "");
  });
}
