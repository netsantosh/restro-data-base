/**
 * Photo service for the restaurant site (separate Apps Script project, deployed as a Web app).
 *
 * When a restaurant photo on the site fails to load (expired Google URL), the page calls:
 *     <web app URL>?place_id=<Google place id>&i=<photo index>&w=<width>
 * and this script asks the Google Places API (New) for a fresh photo of that place and
 * returns  {"url": "...", "author": "..."}.
 *
 * The API key lives only here (Project Settings > Script properties), never on the website.
 *
 * Script properties:
 *   PLACES_API_KEY   Google Cloud API key, restricted to "Places API (New)"
 *   DAILY_LIMIT      optional, max Google calls per day (default 1500) so cost can never run away
 */

var CACHE_SECONDS = 21600;      // 6 hours (the maximum Apps Script allows)

function doGet(e) {
  var p = (e && e.parameter) || {};
  var place = String(p.place_id || '');
  var idx = Math.max(0, Math.min(19, parseInt(p.i, 10) || 0));
  var width = Math.max(200, Math.min(1600, parseInt(p.w, 10) || 800));
  if (!/^[A-Za-z0-9_-]{10,200}$/.test(place)) { return json_({ error: 'bad place_id' }); }

  var cache = CacheService.getScriptCache();
  var cacheKey = 'p:' + place + ':' + idx + ':' + width;
  var hit = cache.get(cacheKey);
  if (hit) { return raw_(hit); }

  var key = PropertiesService.getScriptProperties().getProperty('PLACES_API_KEY');
  if (!key) { return json_({ error: 'PLACES_API_KEY not set' }); }

  // 1) list the photos Google holds for this place (cached, one call per place)
  var listKey = 'n:' + place;
  var names = cache.get(listKey);
  if (!names) {
    if (!allow_()) { return json_({ error: 'daily limit reached' }); }
    var r = UrlFetchApp.fetch('https://places.googleapis.com/v1/places/' + place, {
      headers: { 'X-Goog-Api-Key': key, 'X-Goog-FieldMask': 'photos' }, muteHttpExceptions: true
    });
    if (r.getResponseCode() !== 200) { return json_({ error: 'places ' + r.getResponseCode() }); }
    var d = JSON.parse(r.getContentText());
    names = JSON.stringify((d.photos || []).slice(0, 20).map(function (ph) {
      return { n: ph.name, a: (ph.authorAttributions || []).map(function (x) { return x.displayName; }).join(', ') };
    }));
    cache.put(listKey, names, CACHE_SECONDS);
  }
  var list = JSON.parse(names);
  if (!list[idx]) { return json_({ error: 'no photo' }); }

  // 2) turn the photo reference into an image URL
  if (!allow_()) { return json_({ error: 'daily limit reached' }); }
  var m = UrlFetchApp.fetch('https://places.googleapis.com/v1/' + list[idx].n + '/media?maxWidthPx=' + width + '&skipHttpRedirect=true', {
    headers: { 'X-Goog-Api-Key': key }, muteHttpExceptions: true
  });
  if (m.getResponseCode() !== 200) { return json_({ error: 'media ' + m.getResponseCode() }); }
  var uri = JSON.parse(m.getContentText()).photoUri;
  if (!uri) { return json_({ error: 'no uri' }); }
  var out = JSON.stringify({ url: uri, author: list[idx].a });
  cache.put(cacheKey, out, CACHE_SECONDS);
  return raw_(out);
}

/** Counts Google calls per day and refuses once DAILY_LIMIT is reached. */
function allow_() {
  var props = PropertiesService.getScriptProperties();
  var limit = parseInt(props.getProperty('DAILY_LIMIT'), 10) || 1500;
  var day = Utilities.formatDate(new Date(), 'Asia/Kolkata', 'yyyyMMdd');
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(3000)) { return false; }
  try {
    var k = 'calls_' + day;
    var n = parseInt(props.getProperty(k), 10) || 0;
    if (n >= limit) { return false; }
    props.setProperty(k, String(n + 1));
    return true;
  } finally { lock.releaseLock(); }
}

function raw_(text) { return ContentService.createTextOutput(text).setMimeType(ContentService.MimeType.JSON); }
function json_(obj) { return raw_(JSON.stringify(obj)); }

/** Run once from the editor to test your key: pick any place_id from the sheet. */
function testPlace() {
  var out = doGet({ parameter: { place_id: 'ChIJ8124fBbJ5zsReQibs5i3t5c', i: '0', w: '800' } });
  Logger.log(out.getContent());
}
