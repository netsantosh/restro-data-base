/**
 * Content CMS for the restaurant directory (Spreadsheet 2).
 *
 * Reads the Restaurants DB spreadsheet (Spreadsheet 1), keeps one row per
 * Country / State / City page, writes SEO content with the free Gemini API,
 * and triggers the website build when you press Publish.
 *
 * Menu: Content > 1 Refresh pages and facts | 2 Generate Pending rows |
 *       Regenerate selected row | 3 Publish site
 *
 * Secrets live in Project Settings > Script properties (never in cells):
 *   GEMINI_API_KEY   key from Google AI Studio
 *   GITHUB_TOKEN     fine-grained token, repo access with "Actions: read and write"
 */

var TABS = { Country: 'country', State: 'state', City: 'city' };
var STATUSES = ['Pending', 'Review', 'Approved', 'Hold'];
var FACT_COLS = ['restaurant_count', 'child_count', 'avg_rating', 'top_cuisines', 'top_rated', 'children'];
var EDIT_COLS = ['local_notes', 'baseline_count', 'refresh_flag', 'generated_at', 'notes'];
var CONTENT_COLS = ['h1', 'meta_title', 'meta_description', 'short_intro', 'intro'];
for (var i = 1; i <= 5; i++) { CONTENT_COLS.push('s' + i + '_h2', 's' + i + '_body'); }
for (var j = 1; j <= 5; j++) { CONTENT_COLS.push('faq' + j + '_q', 'faq' + j + '_a'); }
var HEAD = ['path', 'name', 'level', 'status'].concat(FACT_COLS, EDIT_COLS, CONTENT_COLS);

var CONFIG_DEFAULTS = [
  ['DB_SHEET_ID', '', 'ID of the Restaurants DB spreadsheet (the long string in its URL)'],
  ['DB_TAB', 'Listings', 'Tab that holds the scraped rows'],
  ['IGNORE_NAMES', 'Google Maps', 'Listing names to skip, comma separated (scraper capture errors)'],
  ['GEMINI_MODEL', 'gemini-2.5-flash', 'Model name from Google AI Studio (check it is on your free tier)'],
  ['TARGET_WORDS', '800', 'Approximate words per page (intro + 5 sections)'],
  ['SECONDS_BETWEEN_CALLS', '6', 'Pause between Gemini calls to stay inside free-tier rate limits'],
  ['SITE_NAME', 'Restro Guide', 'Used inside meta titles'],
  ['GITHUB_REPO', 'netsantosh/restro-data-base', 'owner/repo that holds the site code'],
  ['GITHUB_WORKFLOW', 'claude-website.yml', 'Workflow file name that builds and deploys the site'],
  ['GITHUB_REF', 'main', 'Branch the workflow runs on']
];

/* ---------------------------------------------------------------- menu */

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Content')
    .addItem('1. Refresh pages and facts', 'refreshPages')
    .addItem('2. Generate content for Pending rows', 'generatePending')
    .addItem('Regenerate selected row', 'regenerateSelected')
    .addSeparator()
    .addItem('3. Publish site', 'publishSite')
    .addSeparator()
    .addItem('Set up tabs (first time)', 'setupSheets')
    .addItem('Test Gemini connection', 'testGemini')
    .addToUi();
}

/* --------------------------------------------------------------- setup */

function setupSheets() {
  var ss = SpreadsheetApp.getActive();
  var cfg = ss.getSheetByName('Config');
  if (!cfg) {
    cfg = ss.insertSheet('Config');
    cfg.getRange(1, 1, 1, 3).setValues([['key', 'value', 'what it is']]).setFontWeight('bold');
    cfg.getRange(2, 1, CONFIG_DEFAULTS.length, 3).setValues(CONFIG_DEFAULTS);
    cfg.setColumnWidth(1, 200); cfg.setColumnWidth(2, 320); cfg.setColumnWidth(3, 520);
    cfg.setFrozenRows(1);
  }
  Object.keys(TABS).forEach(function (title) {
    var sh = ss.getSheetByName(title) || ss.insertSheet(title);
    if (sh.getLastRow() === 0) {
      sh.getRange(1, 1, 1, HEAD.length).setValues([HEAD]);
    }
    formatTab_(sh);
  });
  var blank = ss.getSheetByName('Sheet1');
  if (blank && ss.getSheets().length > 1 && blank.getLastRow() === 0) { ss.deleteSheet(blank); }
  SpreadsheetApp.getUi().alert('Tabs are ready.\n\nNext: fill DB_SHEET_ID in the Config tab, add GEMINI_API_KEY in Project Settings > Script properties, then run Content > 1. Refresh pages and facts.');
}

function formatTab_(sh) {
  var head = sh.getRange(1, 1, 1, HEAD.length);
  head.setFontWeight('bold').setBackground('#111111').setFontColor('#ffffff');
  sh.setFrozenRows(1);
  sh.setFrozenColumns(2);
  var auto = HEAD.indexOf(FACT_COLS[0]) + 1;
  sh.getRange(2, auto, 500, FACT_COLS.length).setBackground('#f3f3f3');
  var statusCol = HEAD.indexOf('status') + 1;
  sh.getRange(2, statusCol, 500, 1).setDataValidation(
    SpreadsheetApp.newDataValidation().requireValueInList(STATUSES, true).setAllowInvalid(false).build());
  sh.getRange(2, 1, 500, HEAD.length).setWrapStrategy(SpreadsheetApp.WrapStrategy.CLIP).setVerticalAlignment('top');
  sh.setColumnWidth(1, 220); sh.setColumnWidth(2, 140); sh.setColumnWidth(3, 70); sh.setColumnWidth(4, 90);
  ['top_cuisines', 'top_rated', 'children', 'local_notes', 'notes', 'h1', 'meta_title', 'meta_description', 'intro']
    .forEach(function (n) { sh.setColumnWidth(HEAD.indexOf(n) + 1, 260); });
}

/* -------------------------------------------------------------- config */

function getConfig_() {
  var sh = SpreadsheetApp.getActive().getSheetByName('Config');
  if (!sh) { throw new Error('Run Content > Set up tabs (first time) first.'); }
  var cfg = {};
  sh.getDataRange().getValues().slice(1).forEach(function (r) { if (r[0]) { cfg[String(r[0]).trim()] = String(r[1]).trim(); } });
  if (!cfg.DB_SHEET_ID) { throw new Error('Fill DB_SHEET_ID in the Config tab.'); }
  return cfg;
}

function prop_(name) {
  var v = PropertiesService.getScriptProperties().getProperty(name);
  if (!v) { throw new Error('Add ' + name + ' in Project Settings > Script properties.'); }
  return v;
}

/* ------------------------------------------------------------ DB reader */

/** Must match slugify() in utils.py so paths line up with the website. */
function slugify_(text) {
  var t = String(text || '').trim().replace(/\([^)]*\)/g, ' ');
  t = t.normalize('NFKD').replace(/[^\x00-\x7F]/g, '').toLowerCase().replace(/&/g, ' and ');
  return t.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

function loadListings_(cfg) {
  var sh = SpreadsheetApp.openById(cfg.DB_SHEET_ID).getSheetByName(cfg.DB_TAB || 'Listings');
  if (!sh) { throw new Error('Tab ' + (cfg.DB_TAB || 'Listings') + ' not found in the DB spreadsheet.'); }
  var lastRow = sh.getLastRow();
  if (lastRow < 2) { return []; }
  var header = sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0].map(function (h) { return String(h).trim(); });
  var wanted = ['name', 'country', 'state', 'city', 'area', 'rating', 'total_reviews', 'cuisine_type', 'place_id', 'scraped_at'];
  // read only the needed columns (the sheet also holds ~40 photo columns)
  var cols = {};
  wanted.forEach(function (n) {
    var c = header.indexOf(n);
    if (c < 0) { throw new Error('Column "' + n + '" not found in the DB tab.'); }
    cols[n] = sh.getRange(2, c + 1, lastRow - 1, 1).getValues();
  });
  var byPlace = {};
  var out = [];
  var ignore = {};
  String(cfg.IGNORE_NAMES || '').split(',').forEach(function (n) { if (n.trim()) { ignore[n.trim().toLowerCase()] = true; } });
  for (var ri = 0; ri < lastRow - 1; ri++) {
    var o = {};
    wanted.forEach(function (n) { o[n] = cols[n][ri][0]; });
    addListing_(o, byPlace, out, ignore);
  }
  return out;
}

function addListing_(o, byPlace, out, ignore) {
  ['name', 'country', 'state', 'city', 'area', 'place_id'].forEach(function (n) { o[n] = String(o[n] || '').trim(); });
  if (!o.name || !o.country || !o.state || !o.city || !o.area || !o.place_id) { return; }
  if (ignore[o.name.toLowerCase()]) { return; }
  o.rating = parseFloat(o.rating) || 0;
  o.reviews = parseInt(String(o.total_reviews).replace(/[^\d]/g, ''), 10) || 0;
  var c = String(o.cuisine_type || '').replace(/\s+restaurants?$/i, '').trim();
  o.cuisine = c.toLowerCase() === 'restaurant' ? '' : c;
  o.scraped_at = String(o.scraped_at || '');
  if (byPlace[o.place_id] !== undefined) {
    if (o.scraped_at >= out[byPlace[o.place_id]].scraped_at) { out[byPlace[o.place_id]] = o; }
    return;
  }
  byPlace[o.place_id] = out.length;
  out.push(o);
}

function buildTree_(listings) {
  var mean = 0, n = 0;
  listings.forEach(function (r) { if (r.rating) { mean += r.rating; n++; } });
  mean = n ? mean / n : 4;
  var nodes = { country: {}, state: {}, city: {} };
  function get(kind, path, name, parent) {
    if (!nodes[kind][path]) { nodes[kind][path] = { kind: kind, path: path, name: name, parent: parent, children: {}, rs: [] }; }
    return nodes[kind][path];
  }
  listings.forEach(function (r) {
    r.score = (r.reviews / (r.reviews + 50)) * r.rating + (50 / (r.reviews + 50)) * mean;
    var cs = slugify_(r.country), ss = slugify_(r.state), ts = slugify_(r.city), as = slugify_(r.area);
    var c = get('country', cs, r.country, null);
    var s = get('state', cs + '/' + ss, r.state, c);
    var t = get('city', cs + '/' + ss + '/' + ts, r.city, s);
    c.children[s.path] = s; s.children[t.path] = t;
    if (!t.children[as]) { t.children[as] = { name: r.area, rs: [] }; }
    t.children[as].rs.push(r);
    c.rs.push(r); s.rs.push(r); t.rs.push(r);
  });
  return nodes;
}

function nodeFacts_(node) {
  var rs = node.rs;
  var rated = rs.filter(function (r) { return r.rating; });
  var avg = rated.length ? Math.round(rated.reduce(function (a, r) { return a + r.rating; }, 0) / rated.length * 10) / 10 : '';
  var cu = {};
  rs.forEach(function (r) { if (r.cuisine) { cu[r.cuisine] = (cu[r.cuisine] || 0) + 1; } });
  var cuisines = Object.keys(cu).sort(function (a, b) { return cu[b] - cu[a]; }).slice(0, 8)
    .map(function (k) { return k + ' (' + cu[k] + ')'; });
  var top = rs.slice().sort(function (a, b) { return b.score - a.score; }).slice(0, 8)
    .map(function (r) {
      return r.name + ' in ' + r.area + ', rated ' + r.rating + (r.reviews ? ' from ' + r.reviews.toLocaleString('en-IN') + ' reviews' : '');
    });
  var kids = Object.keys(node.children).map(function (k) {
    var ch = node.children[k];
    return { name: ch.name, count: ch.rs.length };
  }).sort(function (a, b) { return b.count - a.count; });
  return {
    restaurant_count: rs.length,
    child_count: kids.length,
    avg_rating: avg,
    top_cuisines: cuisines.join('; '),
    top_rated: top.join('; '),
    children: kids.slice(0, 15).map(function (k) { return k.name + ' (' + k.count + ')'; }).join('; ')
  };
}

/* ----------------------------------------------- 1. refresh pages/facts */

function refreshPages() {
  var cfg = getConfig_();
  var tree = buildTree_(loadListings_(cfg));
  var ss = SpreadsheetApp.getActive();
  var added = 0, updated = 0, flagged = 0;
  Object.keys(TABS).forEach(function (title) {
    var kind = TABS[title];
    var sh = ss.getSheetByName(title);
    if (!sh) { throw new Error('Tab ' + title + ' missing. Run Set up tabs first.'); }
    var col = function (n) { return HEAD.indexOf(n) + 1; };
    var last = sh.getLastRow();
    var existing = last > 1 ? sh.getRange(2, 1, last - 1, HEAD.length).getValues() : [];
    var rowOf = {};
    existing.forEach(function (r, i) { rowOf[String(r[0]).trim()] = i; });
    var factVals = [], flagVals = [], newRows = [];
    var paths = Object.keys(tree[kind]).sort();
    // existing rows: refresh facts and the refresh flag
    existing.forEach(function (r) {
      var node = tree[kind][String(r[0]).trim()];
      if (!node) { factVals.push(r.slice(col(FACT_COLS[0]) - 1, col(FACT_COLS[0]) - 1 + FACT_COLS.length)); flagVals.push([r[col('refresh_flag') - 1]]); return; }
      var f = nodeFacts_(node);
      factVals.push(FACT_COLS.map(function (k) { return f[k]; }));
      var base = Number(r[col('baseline_count') - 1]) || 0;
      var flag = (base > 0 && Math.abs(f.restaurant_count - base) / base >= 0.10 && String(r[col('status')-1]) !== 'Pending') ? 'YES' : '';
      if (flag) { flagged++; }
      flagVals.push([flag]);
      updated++;
    });
    if (existing.length) {
      sh.getRange(2, col(FACT_COLS[0]), existing.length, FACT_COLS.length).setValues(factVals);
      sh.getRange(2, col('refresh_flag'), existing.length, 1).setValues(flagVals);
    }
    // new pages
    paths.forEach(function (p) {
      if (rowOf[p] !== undefined) { return; }
      var node = tree[kind][p];
      var f = nodeFacts_(node);
      var row = HEAD.map(function () { return ''; });
      row[0] = p; row[1] = node.name; row[2] = kind; row[3] = 'Pending';
      FACT_COLS.forEach(function (k) { row[col(k) - 1] = f[k]; });
      newRows.push(row); added++;
    });
    if (newRows.length) { sh.getRange(last + 1 > 1 ? last + 1 : 2, 1, newRows.length, HEAD.length).setValues(newRows); }
    formatTab_(sh);
  });
  SpreadsheetApp.getUi().alert('Pages refreshed.\n\nNew pages added: ' + added + '\nExisting rows updated: ' + updated +
    '\nRows flagged for refresh (restaurant count changed 10%+): ' + flagged);
}

/* ------------------------------------------------------ 2. generate text */

function generatePending() {
  var ss = SpreadsheetApp.getActive();
  var cfg = getConfig_();
  var started = Date.now();
  var done = 0, failed = 0, skipped = 0;
  var order = ['Country', 'State', 'City'];
  for (var t = 0; t < order.length; t++) {
    var sh = ss.getSheetByName(order[t]);
    if (!sh || sh.getLastRow() < 2) { continue; }
    var values = sh.getRange(2, 1, sh.getLastRow() - 1, HEAD.length).getValues();
    for (var i = 0; i < values.length; i++) {
      var status = String(values[i][HEAD.indexOf('status')]).trim();
      if (status !== 'Pending') { continue; }
      if (Date.now() - started > 270000) { skipped++; continue; }   // Apps Script stops at 6 min
      try {
        generateRow_(sh, i + 2, values[i], cfg);
        done++;
      } catch (e) {
        failed++;
        sh.getRange(i + 2, HEAD.indexOf('notes') + 1).setValue('ERROR: ' + e.message);
      }
      SpreadsheetApp.flush();
      Utilities.sleep(Number(cfg.SECONDS_BETWEEN_CALLS || 6) * 1000);
    }
  }
  SpreadsheetApp.getUi().alert('Generated: ' + done + '\nFailed: ' + failed +
    (skipped ? '\nNot reached (time limit, run again): ' + skipped : '') +
    '\n\nReview each row, edit if needed, then set status to Approved.');
}

function regenerateSelected() {
  var sh = SpreadsheetApp.getActiveSheet();
  if (!TABS[sh.getName()]) { SpreadsheetApp.getUi().alert('Select a row in the Country, State or City tab.'); return; }
  var row = sh.getActiveRange().getRow();
  if (row < 2) { return; }
  var values = sh.getRange(row, 1, 1, HEAD.length).getValues()[0];
  var status = String(values[HEAD.indexOf('status')]);
  if (status === 'Approved') {
    var ans = SpreadsheetApp.getUi().alert('This row is Approved (live). Regenerating replaces its text and sets it to Review, so it drops off the site until you approve it again. Continue?', SpreadsheetApp.getUi().ButtonSet.YES_NO);
    if (ans !== SpreadsheetApp.getUi().Button.YES) { return; }
  }
  generateRow_(sh, row, values, getConfig_());
  SpreadsheetApp.getUi().alert('Done. Status is now Review.');
}

function generateRow_(sh, rowNum, values, cfg) {
  var get = function (n) { return values[HEAD.indexOf(n)]; };
  var level = String(get('level'));
  var target = Number(cfg.TARGET_WORDS || 800);
  var prompt = buildPrompt_(level, get, cfg, target);
  var factsText = [get('name'), get('restaurant_count'), get('child_count'), get('avg_rating'), get('top_cuisines'),
    get('top_rated'), get('children'), get('local_notes')].join(' ');
  var feedback = '';
  var result = null, problems = [];
  for (var attempt = 0; attempt < 3; attempt++) {
    var obj = callGemini_(prompt + feedback, cfg);
    obj = cleanText_(obj);
    problems = validate_(obj, target);
    result = obj;
    if (problems.length === 0) { break; }
    feedback = '\n\nYour previous answer had these problems, fix them and return the full JSON again: ' + problems.join('; ') + '.';
  }
  var unverified = unverifiedNumbers_(result, factsText);
  var notes = [];
  if (problems.length) { notes.push('Check: ' + problems.join('; ')); }
  if (unverified.length) { notes.push('Numbers not in the data (verify): ' + unverified.join(', ')); }
  notes.push('Words: ' + wordCount_(result));
  var out = {};
  out.h1 = result.h1; out.meta_title = result.meta_title; out.meta_description = result.meta_description;
  out.short_intro = result.short_intro; out.intro = result.intro;
  for (var k = 0; k < 5; k++) {
    var s = (result.sections || [])[k] || {};
    out['s' + (k + 1) + '_h2'] = s.h2 || ''; out['s' + (k + 1) + '_body'] = s.body || '';
    var f = (result.faqs || [])[k] || {};
    out['faq' + (k + 1) + '_q'] = f.q || ''; out['faq' + (k + 1) + '_a'] = f.a || '';
  }
  var c0 = HEAD.indexOf(CONTENT_COLS[0]) + 1;
  sh.getRange(rowNum, c0, 1, CONTENT_COLS.length).setValues([CONTENT_COLS.map(function (n) { return out[n] || ''; })]);
  sh.getRange(rowNum, HEAD.indexOf('status') + 1).setValue('Review');
  sh.getRange(rowNum, HEAD.indexOf('baseline_count') + 1).setValue(get('restaurant_count'));
  sh.getRange(rowNum, HEAD.indexOf('refresh_flag') + 1).setValue('');
  sh.getRange(rowNum, HEAD.indexOf('generated_at') + 1).setValue(new Date());
  sh.getRange(rowNum, HEAD.indexOf('notes') + 1).setValue(notes.join(' | '));
}

/* ------------------------------------------------------------- prompts */

/** Facts for the prompt carry names only: no restaurant counts, ratings or review numbers. */
function stripNumbers_(text) {
  return String(text || '')
    .replace(/,\s*rated [^;]*/g, '')        // "X in Y, rated 4.9 from 1,292 reviews" -> "X in Y"
    .replace(/\s*\(\d[\d,]*\)/g, '')         // "North Indian (115)" -> "North Indian"
    .trim();
}

function buildPrompt_(level, get, cfg, target) {
  var name = get('name');
  var childWord = { country: 'states', state: 'cities', city: 'areas' }[level];
  var secWords = Math.round((target - 115) / 5);
  var outline = {
    country: ['What the restaurant scene looks like in ' + name, 'States to explore for food', 'Popular cuisines across ' + name, 'Highest rated places on our list', 'How to pick a restaurant in ' + name],
    state: ['Eating out in ' + name, 'Cities to explore in ' + name, 'Popular cuisines in ' + name, 'Highest rated places on our list', 'Tips for choosing a restaurant in ' + name],
    city: ['Eating out in ' + name, 'Areas worth exploring in ' + name, 'Popular cuisines in ' + name, 'Highest rated places on our list', 'Tips for choosing a restaurant in ' + name]
  }[level];
  var notes = String(get('local_notes') || '').trim();
  return [
    'You write SEO content for a restaurant directory website. Write the page text for the ' + level + ' page "' + name + '".',
    '',
    'FACTS (the only source of truth, from our database of Google Maps listings):',
    '- Most common cuisines, most common first: ' + stripNumbers_(get('top_cuisines')),
    '- Highest rated places on our list: ' + stripNumbers_(get('top_rated')),
    '- Main ' + childWord + ', largest first: ' + stripNumbers_(get('children')),
    notes ? '- Verified editor notes you may use: ' + notes : '',
    '',
    'RULES:',
    '- Use ONLY the facts above. Do not invent restaurant names, landmarks, history, prices, opening hours, awards or opinions.',
    '- Never say how many restaurants are listed. Do not give ratings, review counts, percentages or any other number. Do not write digits at all.',
    '- You may mention restaurant names, ' + childWord + ' and cuisines from the facts, and naturally link ideas between them.',
    '- Avoid hype and unprovable claims. "Highest rated on our list" is fine, "the best in town" is not.',
    '- Plain, useful English for people choosing where to eat. No filler, no marketing cliches. Do not use em dashes.',
    '- Every section must say something different. Do not repeat sentences or phrases across sections or between short_intro and intro.',
    '- Mention that ratings come from Google Maps listings where it is natural.',
    '',
    'OUTPUT: return ONLY a JSON object with these keys:',
    '- "h1": "Best Restaurants in ' + name + '"',
    '- "meta_title": 60 characters or fewer, include "Restaurants in ' + name + '"',
    '- "meta_description": 130 to 155 characters, specific, no digits',
    '- "short_intro": 45 to 60 words (4 to 5 lines on screen), generic and genuinely useful, shown right under the H1. Start with a natural question or statement about the best restaurants in ' + name + ', say what visitors can find here (kinds of cuisine, areas to explore, how to use the page). No restaurant names, no numbers.',
    '- "intro": 100 to 130 words, 1 or 2 paragraphs separated by a blank line (use \\n\\n). This is the start of the long text shown at the bottom of the page.',
    '- "sections": array of exactly 5 objects {"h2","body"}, each body about ' + secWords + ' words in 1 or 2 paragraphs. Use these H2s, you may adjust wording slightly: ' + JSON.stringify(outline),
    '- "faqs": array of exactly 5 objects {"q","a"}. Questions people really search for about restaurants in ' + name + '. Each answer 35 to 60 words, only from the facts.',
    'Total words in intro plus sections must be about ' + target + '.'
  ].filter(function (l) { return l !== ''; }).join('\n');
}

function callGemini_(prompt, cfg) {
  var key = prop_('GEMINI_API_KEY');
  var url = 'https://generativelanguage.googleapis.com/v1beta/models/' + encodeURIComponent(cfg.GEMINI_MODEL || 'gemini-2.5-flash') + ':generateContent';
  var payload = {
    contents: [{ role: 'user', parts: [{ text: prompt }] }],
    generationConfig: { temperature: 0.7, responseMimeType: 'application/json' }
  };
  var lastErr = '';
  for (var attempt = 0; attempt < 4; attempt++) {
    var res = UrlFetchApp.fetch(url, {
      method: 'post', contentType: 'application/json', headers: { 'x-goog-api-key': key },
      payload: JSON.stringify(payload), muteHttpExceptions: true
    });
    var code = res.getResponseCode();
    if (code === 200) {
      var body = JSON.parse(res.getContentText());
      var parts = (((body.candidates || [])[0] || {}).content || {}).parts || [];
      var text = parts.map(function (p) { return p.text || ''; }).join('');
      text = text.replace(/^```json\s*/i, '').replace(/```\s*$/, '').trim();
      try { return JSON.parse(text); } catch (e) { lastErr = 'Model returned invalid JSON'; }
    } else if (code === 429 || code >= 500) {
      lastErr = 'HTTP ' + code + ' (rate limit or server busy)';
      Utilities.sleep(Math.pow(2, attempt) * 8000);
    } else {
      throw new Error('Gemini HTTP ' + code + ': ' + res.getContentText().slice(0, 300));
    }
  }
  throw new Error(lastErr || 'Gemini failed');
}

function testGemini() {
  var cfg = getConfig_();
  var obj = callGemini_('Return the JSON {"ok": true, "model": "<your model name>"}', cfg);
  SpreadsheetApp.getUi().alert('Gemini replied: ' + JSON.stringify(obj));
}

/* ---------------------------------------------------------- validation */

function cleanText_(o) {
  var fix = function (t) { return String(t || '').replace(/\s*[—–]\s*/g, ', ').replace(/[ \t]+\n/g, '\n').trim(); };
  var out = { h1: fix(o.h1), meta_title: fix(o.meta_title), meta_description: fix(o.meta_description), short_intro: fix(o.short_intro), intro: fix(o.intro), sections: [], faqs: [] };
  (o.sections || []).forEach(function (s) { out.sections.push({ h2: fix(s.h2), body: fix(s.body) }); });
  (o.faqs || []).forEach(function (f) { out.faqs.push({ q: fix(f.q), a: fix(f.a) }); });
  return out;
}

function wordCount_(o) {
  var text = [o.intro].concat((o.sections || []).map(function (s) { return s.body; })).join(' ');
  return text.split(/\s+/).filter(Boolean).length;
}

function validate_(o, target) {
  var p = [];
  if (!o.h1) { p.push('h1 missing'); }
  if (!o.meta_title || o.meta_title.length > 60) { p.push('meta_title must be 60 characters or fewer (now ' + (o.meta_title || '').length + ')'); }
  if (!o.meta_description || o.meta_description.length > 155 || o.meta_description.length < 110) { p.push('meta_description must be 110 to 155 characters (now ' + (o.meta_description || '').length + ')'); }
  if (!o.intro) { p.push('intro missing'); }
  var si = String(o.short_intro || '').split(/\s+/).filter(Boolean).length;
  if (si < 35 || si > 75) { p.push('short_intro must be 45 to 60 words (now ' + si + ')'); }
  var allText = [o.h1, o.meta_title, o.meta_description, o.short_intro, o.intro].concat(
    (o.sections || []).map(function (s) { return s.h2 + ' ' + s.body; }),
    (o.faqs || []).map(function (f) { return f.q + ' ' + f.a; })).join(' ');
  if (/\d/.test(allText)) { p.push('contains digits; remove every number (restaurant counts and ratings must not appear)'); }
  if ((o.sections || []).length !== 5) { p.push('need exactly 5 sections'); }
  if ((o.faqs || []).length !== 5) { p.push('need exactly 5 faqs'); }
  var words = wordCount_(o);
  if (words < Math.round(target * 0.85)) { p.push('too short, ' + words + ' words, need about ' + target); }
  if (words > Math.round(target * 1.25)) { p.push('too long, ' + words + ' words, need about ' + target); }
  return p;
}

function unverifiedNumbers_(o, factsText) {
  var all = [o.h1, o.meta_title, o.meta_description, o.intro].concat(
    (o.sections || []).map(function (s) { return s.h2 + ' ' + s.body; }),
    (o.faqs || []).map(function (f) { return f.q + ' ' + f.a; })).join(' ');
  var norm = function (m) { return m.replace(/,/g, '').replace(/\.0$/, ''); };
  var known = {};
  (String(factsText).match(/\d[\d,]*\.?\d*/g) || []).forEach(function (m) { known[norm(m)] = true; });
  var bad = {};
  (all.match(/\d[\d,]*\.?\d*/g) || []).forEach(function (m) {
    var n = norm(m);
    if (!known[n] && !(Number(n) >= 1 && Number(n) <= 10) && n !== '60' && n !== '5') { bad[m] = true; }
  });
  return Object.keys(bad);
}

/* --------------------------------------------------------- 3. publish */

function publishSite() {
  var ui = SpreadsheetApp.getUi();
  var cfg = getConfig_();
  var ss = SpreadsheetApp.getActive();
  var counts = { Approved: 0, Review: 0, Pending: 0, Hold: 0 };
  Object.keys(TABS).forEach(function (title) {
    var sh = ss.getSheetByName(title);
    if (!sh || sh.getLastRow() < 2) { return; }
    sh.getRange(2, HEAD.indexOf('status') + 1, sh.getLastRow() - 1, 1).getValues().forEach(function (r) {
      var s = String(r[0]).trim(); if (counts[s] !== undefined) { counts[s]++; }
    });
  });
  var ans = ui.alert('Publish the site now?\n\nApproved pages (go live): ' + counts.Approved +
    '\nReview / Pending / Hold (stay hidden): ' + counts.Review + ' / ' + counts.Pending + ' / ' + counts.Hold +
    '\n\nThe build reads the latest restaurant database and this sheet.', ui.ButtonSet.YES_NO);
  if (ans !== ui.Button.YES) { return; }
  var url = 'https://api.github.com/repos/' + cfg.GITHUB_REPO + '/actions/workflows/' + cfg.GITHUB_WORKFLOW + '/dispatches';
  var res = UrlFetchApp.fetch(url, {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    headers: { Authorization: 'Bearer ' + prop_('GITHUB_TOKEN'), Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' },
    payload: JSON.stringify({ ref: cfg.GITHUB_REF || 'main' })
  });
  if (res.getResponseCode() === 204) {
    ui.alert('Build started. It takes a few minutes.\n\nProgress: https://github.com/' + cfg.GITHUB_REPO + '/actions');
  } else {
    ui.alert('GitHub said ' + res.getResponseCode() + ':\n' + res.getContentText().slice(0, 400));
  }
}
