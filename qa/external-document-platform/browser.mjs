import { readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');
const input = JSON.parse(await readFile(process.argv[2], 'utf8'));
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
const errors = [];
page.on('pageerror', e => errors.push(String(e)));
try {
  await page.goto(input.public_url + '/editor?draft_id=' + input.draft_id);
  await page.locator('.connection-code').waitFor();
  await page.waitForFunction(() => document.querySelector('.connection-code')?.textContent?.length === 8);
  const code = await page.locator('.connection-code').innerText();
  const response = await fetch(input.public_url + '/host/tools/connect_document_editor', {
    method: 'POST', headers: { authorization: 'Bearer ' + input.connector_token, 'content-type': 'application/json' },
    body: JSON.stringify({ user_id: 'writer', arguments: { code } }),
  });
  if (!response.ok) throw new Error('editor_pairing_' + response.status);
  let lostAutosave = false;
  let lostCommit = false;
  await page.route('**/editor/session/*/tools', async route => {
    const body = route.request().postDataJSON();
    if ((body.name === 'update_document_draft' && !lostAutosave) || (body.name === 'commit_document_draft' && !lostCommit)) {
      await route.fetch();
      if (body.name === 'update_document_draft') lostAutosave = true;
      else lostCommit = true;
      await route.abort('failed');
    } else await route.continue();
  });
  const paragraph = page.locator('[contenteditable=true]').filter({ hasText: 'Original paragraph' }).first();
  await paragraph.waitFor();
  await page.getByRole("img", {name:"receipt",exact:true}).waitFor();
  await paragraph.click();
  await paragraph.press('End');
  await page.keyboard.insertText(' Human receipt');
  await page.getByRole('button', {name: '同じ操作で再試行', exact: true}).waitFor();
  await page.getByRole('button', { name: 'ソース編集', exact: true }).click();
  const localSource = page.getByRole('textbox', { name: 'Markdownソース', exact: true });
  const beforeRetry = await localSource.inputValue();
  await localSource.fill(beforeRetry.replace('Human receipt', 'Human receipt Newer input'));
  if (await page.getByRole('button', {name:'保存版を確定',exact:true}).isEnabled()) throw new Error('unconfirmed_write_not_blocked');
  await page.getByRole('button', {name: '同じ操作で再試行', exact: true}).click();
  if (!(await localSource.inputValue()).includes('Newer input')) throw new Error('newer_input_lost_on_replay');
  await page.waitForFunction(() => document.querySelector('[role=status]')?.textContent?.includes('下書き保存済み') && !document.querySelector('[role=status]')?.textContent?.includes('未保存'));
  await page.getByRole('button', { name: 'ソース編集', exact: true }).click();
  const source = await page.getByRole('textbox', { name: 'Markdownソース', exact: true }).inputValue();
  if (!source.includes('Human receipt') || !source.includes(':::message') || !source.includes('graph TD') || !source.includes('<Component')) throw new Error('raw_blocks_lost');
  await page.getByRole('button', { name: '保存版を確定', exact: true }).click();
  await page.getByRole('button', {name: '同じ操作で再試行', exact: true}).waitFor();
  await page.getByRole('button', {name: '同じ操作で再試行', exact: true}).click();
  await page.waitForFunction(() => document.querySelector('[role=status]')?.textContent?.includes('保存版確定済み'));
  await page.getByRole('button', {name:'見たまま編集',exact:true}).click();
  await page.screenshot({ path: input.screenshot, fullPage: true });
  if (errors.length) throw new Error('browser_error_' + errors.join(';'));
  const creation = await browser.newPage();
  await creation.goto(input.public_url + '/editor?scope=' + encodeURIComponent(JSON.stringify({collection_id:'synthetic'})));
  await creation.waitForFunction(() => document.querySelector('.connection-code')?.textContent?.length === 8);
  const createCode = await creation.locator('.connection-code').innerText();
  const approval = await fetch(input.public_url + '/host/tools/connect_document_editor', {
    method:'POST', headers:{authorization:'Bearer ' + input.connector_token,'content-type':'application/json'},
    body:JSON.stringify({user_id:'writer',arguments:{code:createCode}}),
  });
  if (!approval.ok) throw new Error('create_pairing_failed');
  let lostCreation = false;
  const createRequests = [];
  await creation.route('**/editor/session/*/tools', async route => {
    const body = route.request().postDataJSON();
    if (body.name === 'create_document_draft') {
      createRequests.push(body.arguments);
      if (!lostCreation) {
        await route.fetch(); lostCreation = true; await route.abort('failed'); return;
      }
    }
    await route.continue();
  });
  await creation.getByRole('button', {name:'新しい下書き',exact:true}).waitFor();
  await creation.getByRole('textbox', {name:'タイトル',exact:true}).fill('Creation recovery');
  await creation.getByRole('button', {name:'新しい下書き',exact:true}).click();
  await creation.getByRole('button', {name:'同じ操作で再試行',exact:true}).waitFor();
  await creation.getByRole('button', {name:'同じ操作で再試行',exact:true}).click();
  await creation.getByRole('button', {name:'保存版を確定',exact:true}).waitFor();
  if (createRequests.length !== 2 || JSON.stringify(createRequests[0]) !== JSON.stringify(createRequests[1])) throw new Error('creation_replay_changed');
  await creation.close();
  await writeFile(input.result_file, JSON.stringify({ wysiwyg_edit: true, protected_blocks: true, commit: true, lost_autosave_response:lostAutosave, lost_commit_response:lostCommit, lost_creation_response:lostCreation, newer_input_preserved_on_replay:true, page_errors: errors }));
} finally { await browser.close(); }
