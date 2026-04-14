#!/usr/bin/env node
/**
 * Scrape PineScript v6 documentation from TradingView.
 * Uses Playwright to render each page and extract markdown content.
 * Usage: node scripts/dev/scrape_docs.mjs
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const OUT_DIR = '/Users/fractalyst/pinescript_mcp/data/pinescriptv6/scraped_v6_docs';

const PAGES = [
  // Primer
  ['primer/first-steps', '/pine-script-docs/primer/first-steps'],
  ['primer/first-indicator', '/pine-script-docs/primer/first-indicator'],
  ['primer/next-steps', '/pine-script-docs/primer/next-steps'],
  // Language
  ['language/arrays', '/pine-script-docs/language/arrays'],
  ['language/built-ins', '/pine-script-docs/language/built-ins'],
  ['language/conditional-structures', '/pine-script-docs/language/conditional-structures'],
  ['language/declaration-statements', '/pine-script-docs/language/declaration-statements'],
  ['language/enums', '/pine-script-docs/language/enums'],
  ['language/identifiers', '/pine-script-docs/language/identifiers'],
  ['language/loops', '/pine-script-docs/language/loops'],
  ['language/maps', '/pine-script-docs/language/maps'],
  ['language/matrices', '/pine-script-docs/language/matrices'],
  ['language/script-structure', '/pine-script-docs/language/script-structure'],
  ['language/type-system', '/pine-script-docs/language/type-system'],
  ['language/user-defined-functions', '/pine-script-docs/language/user-defined-functions'],
  ['language/variable-declarations', '/pine-script-docs/language/variable-declarations'],
  // Concepts
  ['concepts/alerts', '/pine-script-docs/concepts/alerts'],
  ['concepts/bar-states', '/pine-script-docs/concepts/bar-states'],
  ['concepts/chart-information', '/pine-script-docs/concepts/chart-information'],
  ['concepts/inputs', '/pine-script-docs/concepts/inputs'],
  ['concepts/libraries', '/pine-script-docs/concepts/libraries'],
  ['concepts/non-standard-charts-data', '/pine-script-docs/concepts/non-standard-charts-data'],
  ['concepts/other-timeframes-and-data', '/pine-script-docs/concepts/other-timeframes-and-data'],
  ['concepts/repainting', '/pine-script-docs/concepts/repainting'],
  ['concepts/sessions', '/pine-script-docs/concepts/sessions'],
  ['concepts/strategies', '/pine-script-docs/concepts/strategies'],
  ['concepts/strings', '/pine-script-docs/concepts/strings'],
  ['concepts/time', '/pine-script-docs/concepts/time'],
  // Errors
  ['errors/overview', '/pine-script-docs/errors/overview'],
  ['errors/CE10101', '/pine-script-docs/errors/CE10101'],
  ['errors/CW10003', '/pine-script-docs/errors/CW10003'],
  ['errors/RE10139', '/pine-script-docs/errors/RE10139'],
  ['errors/RE10143', '/pine-script-docs/errors/RE10143'],
  // FAQ
  ['faq/general', '/pine-script-docs/faq/general'],
  ['faq/programming', '/pine-script-docs/faq/programming'],
  ['faq/variables-and-operators', '/pine-script-docs/faq/variables-and-operators'],
  ['faq/functions', '/pine-script-docs/faq/functions'],
  ['faq/data-structures', '/pine-script-docs/faq/data-structures'],
  ['faq/indicators', '/pine-script-docs/faq/indicators'],
  ['faq/strategies', '/pine-script-docs/faq/strategies'],
  ['faq/alerts', '/pine-script-docs/faq/alerts'],
  ['faq/other-data-and-timeframes', '/pine-script-docs/faq/other-data-and-timeframes'],
  ['faq/strings-and-formatting', '/pine-script-docs/faq/strings-and-formatting'],
  ['faq/times-dates-and-sessions', '/pine-script-docs/faq/times-dates-and-sessions'],
  ['faq/visuals', '/pine-script-docs/faq/visuals'],
  ['faq/techniques', '/pine-script-docs/faq/techniques'],
  // Migration
  ['migration-guides/overview', '/pine-script-docs/migration-guides/overview'],
  ['migration-guides/to-pine-version-6', '/pine-script-docs/migration-guides/to-pine-version-6'],
  // Writing
  ['writing/limitations', '/pine-script-docs/writing/limitations'],
  ['writing/profiling-and-optimization', '/pine-script-docs/writing/profiling-and-optimization'],
  ['writing/style-guide', '/pine-script-docs/writing/style-guide'],
  ['writing/debugging', '/pine-script-docs/writing/debugging'],
  ['writing/publishing', '/pine-script-docs/writing/publishing'],
  // Release notes
  ['release-notes', '/pine-script-docs/release-notes'],
  // Visuals
  ['visuals/overview', '/pine-script-docs/visuals/overview'],
  ['visuals/plots', '/pine-script-docs/visuals/plots'],
  ['visuals/lines-and-boxes', '/pine-script-docs/visuals/lines-and-boxes'],
  ['visuals/tables', '/pine-script-docs/visuals/tables'],
  ['visuals/backgrounds', '/pine-script-docs/visuals/backgrounds'],
  ['visuals/bar-coloring', '/pine-script-docs/visuals/bar-coloring'],
  ['visuals/bar-plotting', '/pine-script-docs/visuals/bar-plotting'],
  ['visuals/colors', '/pine-script-docs/visuals/colors'],
  ['visuals/fills', '/pine-script-docs/visuals/fills'],
  ['visuals/levels', '/pine-script-docs/visuals/levels'],
  ['visuals/text-and-shapes', '/pine-script-docs/visuals/text-and-shapes'],
];

const EXTRACT_FN = `
() => {
  const main = document.querySelector('main .vp-doc') || document.querySelector('article') || document.querySelector('main');
  if (!main) return null;

  function walk(el) {
    let r = '';
    for (const child of el.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) {
        const t = child.textContent;
        if (t.trim()) r += t;
      } else if (child.nodeType === Node.ELEMENT_NODE) {
        const tag = child.tagName.toLowerCase();
        if (tag === 'style' || tag === 'script' || tag === 'nav') continue;
        if (tag === 'pre') {
          const code = child.querySelector('code');
          const lang = (code?.className?.match(/language-(\\w+)/)?.[1]) || '';
          const text = code?.textContent || child.textContent;
          r += '\\n\\\`\\\`\\\`' + lang + '\\n' + text + '\\n\\\`\\\`\\\`\\n';
        } else if (tag === 'code' && !child.closest('pre')) {
          r += '\\\`' + child.textContent + '\\\`';
        } else if (/^h[1-4]$/.test(tag)) {
          const level = parseInt(tag[1]);
          r += '\\n' + '#'.repeat(level) + ' ' + child.textContent.trim() + '\\n\\n';
        } else if (tag === 'p') {
          r += child.textContent.trim() + '\\n\\n';
        } else if (tag === 'ul' || tag === 'ol') {
          for (const li of child.querySelectorAll(':scope > li')) {
            r += '- ' + li.textContent.trim() + '\\n';
          }
          r += '\\n';
        } else if (tag === 'table') {
          const rows = child.querySelectorAll('tr');
          for (let i = 0; i < rows.length; i++) {
            const cells = rows[i].querySelectorAll('th, td');
            r += '| ' + Array.from(cells).map(c => c.textContent.trim().replace(/\\n/g, ' ')).join(' | ') + ' |\\n';
            if (i === 0) r += '| ' + Array.from(cells).map(() => '---').join(' | ') + ' |\\n';
          }
          r += '\\n';
        } else {
          r += walk(child);
        }
      }
    }
    return r;
  }

  const title = document.querySelector('h1')?.textContent?.trim() || '';
  const url = window.location.pathname;
  const content = walk(main);
  return { title, url, content: '# ' + title + '\\n\\nSource: https://www.tradingview.com' + url + '\\n\\n' + content };
}
`;

async function main() {
  console.log(`Scraping ${PAGES.length} pages from TradingView PineScript v6 docs...`);

  // Ensure output dirs exist
  const dirs = new Set(PAGES.map(([name]) => path.dirname(name)));
  for (const dir of dirs) {
    fs.mkdirSync(path.join(OUT_DIR, dir), { recursive: true });
  }

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
  });
  const page = await context.newPage();

  let saved = 0;
  let failed = 0;

  for (const [name, urlPath] of PAGES) {
    const outPath = path.join(OUT_DIR, name + '.md');
    try {
      await page.goto('https://www.tradingview.com' + urlPath, {
        waitUntil: 'networkidle',
        timeout: 30000,
      });
      // Wait for Astro hydration and content render
      await page.waitForSelector('h1', { timeout: 10000 }).catch(() => {});
      await page.waitForTimeout(1000);

      // Try multiple content selectors
      const data = await page.evaluate(() => {
        const main = document.querySelector('.vp-doc') ||
                     document.querySelector('article') ||
                     document.querySelector('[role="main"]') ||
                     document.querySelector('main main') ||
                     document.querySelector('main');
        if (!main) {
          // Debug: return available selectors
          const tags = {};
          document.querySelectorAll('main, article, .vp-doc, [role="main"]').forEach(el => {
            tags[el.tagName + '.' + el.className.substring(0, 50)] = el.textContent.length;
          });
          return { title: document.title, debug: tags, content: null };
        }

        function walk(el) {
          let r = '';
          for (const child of el.childNodes) {
            if (child.nodeType === Node.TEXT_NODE) {
              const t = child.textContent;
              if (t.trim()) r += t;
            } else if (child.nodeType === Node.ELEMENT_NODE) {
              const tag = child.tagName.toLowerCase();
              if (tag === 'style' || tag === 'script' || tag === 'nav') continue;
              if (tag === 'pre') {
                const code = child.querySelector('code');
                const lang = (code?.className?.match(/language-(\w+)/)?.[1]) || '';
                const text = code?.textContent || child.textContent;
                r += '\n```' + lang + '\n' + text + '\n```\n';
              } else if (tag === 'code' && !child.closest('pre')) {
                r += '`' + child.textContent + '`';
              } else if (/^h[1-4]$/.test(tag)) {
                const level = parseInt(tag[1]);
                r += '\n' + '#'.repeat(level) + ' ' + child.textContent.trim() + '\n\n';
              } else if (tag === 'p') {
                r += child.textContent.trim() + '\n\n';
              } else if (tag === 'ul' || tag === 'ol') {
                for (const li of child.querySelectorAll(':scope > li')) {
                  r += '- ' + li.textContent.trim() + '\n';
                }
                r += '\n';
              } else if (tag === 'table') {
                const rows = child.querySelectorAll('tr');
                for (let i = 0; i < rows.length; i++) {
                  const cells = rows[i].querySelectorAll('th, td');
                  r += '| ' + Array.from(cells).map(c => c.textContent.trim().replace(/\n/g, ' ')).join(' | ') + ' |\n';
                  if (i === 0) r += '| ' + Array.from(cells).map(() => '---').join(' | ') + ' |\n';
                }
                r += '\n';
              } else {
                r += walk(child);
              }
            }
          }
          return r;
        }

        const title = document.querySelector('h1')?.textContent?.trim() || '';
        const url = window.location.pathname;
        const content = walk(main);
        return { title, url, content: '# ' + title + '\n\nSource: https://www.tradingview.com' + url + '\n\n' + content };
      });
      if (data && data.content && data.content.length > 50) {
        fs.writeFileSync(outPath, data.content, 'utf8');
        saved++;
        console.log(`  OK ${name} (${(data.content.length / 1024).toFixed(1)}KB)`);
      } else {
        failed++;
        console.log(`  SKIP ${name} (content too short or empty)`);
      }
    } catch (e) {
      failed++;
      console.log(`  FAIL ${name}: ${e.message?.substring(0, 80)}`);
    }
  }

  await browser.close();
  console.log(`\nDone: ${saved} saved, ${failed} failed out of ${PAGES.length} pages`);
}

main().catch(e => { console.error(e); process.exit(1); });
