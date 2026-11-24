// Mede e fotografa uma página local em 1440x900 (desk) e 390x844 (mob).
//
// Uso:  node medir.js <arquivo.html> <pasta de saída> [prefixo]
//
// Grava <pasta>/<prefixo>-desk-NNdeMM.png, <prefixo>-mob-NNdeMM.png (uma foto
// por tela, página inteira) e <pasta>/<prefixo>-medidas.json com: altura
// total, telas, palavras visíveis, links, controles, e a posição de cada bloco
// de primeiro nível dentro de <main> (e do cabeçalho/rodapé).
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  const [arquivo, pasta, prefixo = 'home'] = process.argv.slice(2);
  if (!arquivo || !pasta) { console.error('uso: node medir.js <arquivo.html> <pasta> [prefixo]'); process.exit(2); }
  fs.mkdirSync(pasta, { recursive: true });
  const url = 'file://' + path.resolve(arquivo);
  const b = await chromium.launch();
  const medidas = {};
  for (const [vp, w, h] of [['desk', 1440, 900], ['mob', 390, 844]]) {
    const ctx = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
    const pg = await ctx.newPage();
    const erros = [];
    pg.on('pageerror', e => erros.push(String(e)));
    await pg.goto(url, { waitUntil: 'load' });
    await pg.waitForTimeout(900);
    const H = await pg.evaluate(() => document.documentElement.scrollHeight);
    const n = Math.max(1, Math.ceil(H / h));
    for (let i = 0; i < n; i++) {
      await pg.evaluate(y => window.scrollTo(0, y), i * h);
      await pg.waitForTimeout(100);
      await pg.screenshot({ path: path.join(pasta, `${prefixo}-${vp}-${String(i + 1).padStart(2, '0')}de${String(n).padStart(2, '0')}.png`) });
    }
    await pg.evaluate(() => window.scrollTo(0, 0));
    medidas[vp] = await pg.evaluate((h) => {
      const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.height > 0 && r.width > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
      const blocos = [];
      const tops = [...document.querySelectorAll('body > header, body > footer, main > *, body > section, body > nav, body > div')].filter(vis);
      for (const el of tops) {
        const r = el.getBoundingClientRect();
        if (r.height < 8) continue;
        blocos.push({
          tag: el.tagName.toLowerCase(), id: el.id || '', cls: String(el.className && el.className.baseVal === undefined ? el.className : '').slice(0, 50),
          topo: Math.round(r.top + scrollY), altura: Math.round(r.height), telas: +(r.height / h).toFixed(2),
          links: [...el.querySelectorAll('a')].filter(vis).length,
          palavras: (el.innerText || '').split(/\s+/).filter(Boolean).length,
          texto: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 80),
        });
      }
      const h1 = document.querySelector('main h1, main h2');
      const linksVis = [...document.querySelectorAll('a')].filter(vis).length;
      const controles = [...document.querySelectorAll('button, [role=tab], select, input, summary')].filter(vis).length;
      const sw = document.documentElement.scrollWidth;
      return {
        altura_total: document.documentElement.scrollHeight,
        telas: +(document.documentElement.scrollHeight / h).toFixed(2),
        largura_rolavel: sw, estoura_na_horizontal: sw > innerWidth + 1,
        palavras_visiveis: (document.body.innerText || '').split(/\s+/).filter(Boolean).length,
        links_visiveis: linksVis, links_no_codigo: document.querySelectorAll('a').length,
        controles_visiveis: controles,
        primeiro_titulo: h1 ? { texto: (h1.innerText || '').slice(0, 80), topo: Math.round(h1.getBoundingClientRect().top + scrollY), base: Math.round(h1.getBoundingClientRect().bottom + scrollY) } : null,
        blocos,
      };
    }, h);
    medidas[vp].erros_de_script = erros;
    await ctx.close();
  }
  medidas.bytes = fs.statSync(path.resolve(arquivo)).size;
  fs.writeFileSync(path.join(pasta, `${prefixo}-medidas.json`), JSON.stringify(medidas, null, 1));
  console.log(JSON.stringify({ bytes: medidas.bytes, desk: { altura: medidas.desk.altura_total, telas: medidas.desk.telas, palavras: medidas.desk.palavras_visiveis, links: medidas.desk.links_visiveis, controles: medidas.desk.controles_visiveis, titulo: medidas.desk.primeiro_titulo, erros: medidas.desk.erros_de_script.length }, mob: { altura: medidas.mob.altura_total, telas: medidas.mob.telas, palavras: medidas.mob.palavras_visiveis, titulo: medidas.mob.primeiro_titulo, estoura: medidas.mob.estoura_na_horizontal, erros: medidas.mob.erros_de_script.length } }, null, 1));
  await b.close();
})();
