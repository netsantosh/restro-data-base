/**
 * Contact form handler (Google Apps Script web app).
 *
 * The website posts the form here. The script emails the message to YOUR address, which is stored only in
 * Script Properties (key TO_EMAIL), so the address never appears in the website or in this repository.
 *
 * Setup: see README, "Contact form". Deploy as Web app: Execute as "Me", Who has access "Anyone".
 */
var MAX_PER_HOUR = 30;      // all senders together
var MAX_PER_SENDER = 3;     // per email address per hour

function doPost(e) {
  try {
    var p = (e && e.parameter) || {};
    if (p.website) return reply_('ok');                          // honeypot filled in: bot, pretend success
    var name = clean_(p.name, 80), email = clean_(p.email, 120), topic = clean_(p.topic, 80);
    var page = clean_(p.page, 300), message = clean_(p.message, 3000);
    if (!name || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email) || message.length < 10) return reply_('invalid');
    if (limited_(email)) return reply_('limited');
    var to = PropertiesService.getScriptProperties().getProperty('TO_EMAIL');
    if (!to) return reply_('not configured');
    var body = 'From: ' + name + ' <' + email + '>\n' +
               'Topic: ' + (topic || '-') + '\n' +
               'Page: ' + (page || '-') + '\n\n' + message + '\n';
    MailApp.sendEmail({ to: to, replyTo: email, subject: '[Vanmeya] ' + (topic || 'Message') + ' from ' + name,
                        body: body, name: 'Vanmeya contact form' });
    return reply_('ok');
  } catch (err) {
    return reply_('error');
  }
}

function doGet() { return reply_('contact form is running'); }

function clean_(v, max) {
  return String(v || '').replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '').trim().slice(0, max);
}

function limited_(email) {
  var cache = CacheService.getScriptCache();
  var keys = ['all', 'e:' + email.toLowerCase()], caps = [MAX_PER_HOUR, MAX_PER_SENDER], hit = false;
  for (var i = 0; i < keys.length; i++) {
    var n = Number(cache.get(keys[i]) || 0);
    if (n >= caps[i]) hit = true;
    cache.put(keys[i], String(n + 1), 3600);
  }
  return hit;
}

function reply_(text) {
  return ContentService.createTextOutput(text).setMimeType(ContentService.MimeType.TEXT);
}
