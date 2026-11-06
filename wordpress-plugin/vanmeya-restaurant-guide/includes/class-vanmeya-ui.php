<?php
if (!defined('ABSPATH')) { exit; }

/** The look and the screens of the Vanmeya admin: Start here, Photos, Website, Settings. */
class Vanmeya_UI {
    public static function css() {
        return '<style>
.vm{max-width:1040px}.vm h1{font-size:26px;margin-bottom:6px}
.vm-lead{color:#50575e;font-size:14px;margin:0 0 14px}
.vm-card{background:#fff;border:1px solid #dcdcde;border-radius:12px;padding:18px 22px;margin:16px 0;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.vm-card h2{margin:0 0 4px;font-size:17px;padding:0}.vm-card h3{margin:14px 0 6px}
.vm-sub{color:#50575e;margin:0 0 12px}
.vm-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}.vm-grid .vm-card{margin:0}
.vm-pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600;vertical-align:middle}
.vm-ok{background:#e6f4ea;color:#1a7f37}.vm-warn{background:#fff4d6;color:#8a5a00}.vm-bad{background:#fde8e8;color:#b32d2e}.vm-info{background:#e8f0fe;color:#2150b4}.vm-mute{background:#eef0f2;color:#50575e}
.vm-bar{height:14px;background:#eef0f2;border-radius:7px;overflow:hidden;display:flex;margin:10px 0}.vm-bar i{display:block;height:100%;transition:width .4s}
.vm-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:8px 0}.vm-stat{background:#f6f7f7;border-radius:10px;padding:10px 14px}.vm-stat b{font-size:24px;display:block;line-height:1.2}.vm-stat span{color:#50575e;font-size:12px}
.vm-dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}
.vm-flow{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;counter-reset:s}.vm-flow div{background:#f6f7f7;border-radius:10px;padding:12px 14px 12px 48px;position:relative;min-height:54px}
.vm-flow div:before{counter-increment:s;content:counter(s);position:absolute;left:12px;top:12px;width:26px;height:26px;border-radius:50%;background:#1d2327;color:#fff;font-weight:700;text-align:center;line-height:26px}
.vm-big{font-size:14px!important;padding:4px 20px!important;height:auto!important;min-height:38px}
.vm-live{display:none;background:#e8f0fe;border:1px solid #b9cdf5;color:#1d2327;border-radius:10px;padding:12px 16px;margin:12px 0;font-weight:600}.vm-live.bad{background:#fde8e8;border-color:#f3b9b9;color:#8a1f1f}.vm-live.good{background:#e6f4ea;border-color:#b5dcc0;color:#145c2a}
.vm-file{background:#fafafa;border:2px dashed #c3c4c7;border-radius:10px;padding:16px;margin:8px 0}
.vm-table{width:100%;border-collapse:collapse}.vm-table td,.vm-table th{padding:9px 10px;border-bottom:1px solid #eef0f2;text-align:left;vertical-align:top}
.vm details summary{cursor:pointer;color:#2271b1;margin:8px 0}
.vm-help{background:#f0f6fc;border-left:4px solid #72aee6;padding:10px 14px;max-width:900px;margin:10px 0;border-radius:0 8px 8px 0}
.vm .nav-tab-wrapper{margin-bottom:10px}.vm .widefat{border-radius:8px;overflow:hidden}
</style>';
    }

    private static function pill($text, $cls) { return '<span class="vm-pill vm-' . $cls . '">' . esc_html($text) . '</span>'; }
    private static function link($page, $label, $class = 'button', $args = []) { return '<a class="' . esc_attr($class) . '" href="' . esc_url(add_query_arg(array_merge(['page' => $page], $args), admin_url('admin.php'))) . '">' . esc_html($label) . '</a>'; }
    private static function hidden($action, $nonce) { return '<input type="hidden" name="action" value="' . esc_attr($action) . '"><input type="hidden" name="_wpnonce" value="' . esc_attr($nonce) . '">'; }

    /** Plain-English meaning of a failure note. */
    public static function reason($note) {
        $n = (string)$note; $b = strpos($n, 'browser_') === 0 ? substr($n, 8) : $n;
        if (strpos($b, 'too_small') !== false) return ['Too small to look good (logos, icons, tiny thumbnails)', 'Skipped on purpose. Nothing to do.', 'mute'];
        if (strpos($b, 'not_an_image') !== false) return ['Google sent something that is not a picture', 'Skipped on purpose.', 'mute'];
        if ($b === 'blocked') return ['Google did not hand over the picture (busy or blocked)', 'Press "Retry problem photos". It now goes slower and tries again.', 'warn'];
        if ($b === 'nocors') return ['Google shows the picture but does not let other websites copy it', 'Cannot be fixed from the browser. Press "Retry problem photos" to let the hosting server try.', 'warn'];
        if ($b === 'gone') return ['Google no longer has this picture', 'Run the GitHub photos workflow again and upload the newer images file.', 'bad'];
        if (strpos($b, 'http_404') !== false || strpos($b, 'http_410') !== false) return ['The photo link has expired or the photo was removed', 'Run the GitHub photos workflow again and upload the newer images file.', 'bad'];
        if (strpos($b, 'http_403') !== false) return ['Google refused the download', 'Try again later, or upload a newer images file.', 'warn'];
        if (strpos($b, 'net:') === 0 || $b === 'no_connection') return ['The hosting could not connect to Google', 'Use "Download photos": your browser takes over automatically.', 'warn'];
        return [$b === '' ? 'Other problem' : 'Other problem (' . $b . ')', 'Press "Retry problem photos".', 'warn'];
    }

    /** One status line per area, used on Start here. */
    private static function card_photos($c) {
        $total = max(1, $c['done'] + $c['pending'] + $c['failed']); $problems = max(0, $c['failed'] - $c['skipped']);
        $pill = $c['done'] === 0 ? self::pill('Not started', 'mute') : ($c['pending'] > 0 ? self::pill('In progress', 'info') : ($problems > 0 ? self::pill('Done, some problems', 'warn') : self::pill('Done', 'ok')));
        return '<div class="vm-card"><h2>Photos ' . $pill . '</h2><p class="vm-sub">Restaurant pictures saved on your hosting.</p><p style="font-size:22px;margin:4px 0"><strong>' . number_format_i18n($c['done']) . '</strong> <span class="description">saved of ' . number_format_i18n($total) . '</span></p>'
            . '<div class="vm-bar"><i style="width:' . round(100 * $c['done'] / $total) . '%;background:#1a7f37"></i></div>' . self::link('vanmeya-photos', 'Open Photos', 'button button-primary') . '</div>';
    }

    public static function dashboard($c, $installed, $pages) {
        $seo = Vanmeya_Seo_Admin::settings(); $hidden = (int)Vanmeya_WP::opt('noindex', 1);
        echo '<p class="vm-lead">Everything for your restaurant guide in one place. Start with the cards below.</p><div class="vm-grid">';
        echo self::card_photos($c);
        echo '<div class="vm-card"><h2>Website ' . ($installed ? self::pill('Installed', 'ok') : self::pill('Not installed', 'bad')) . '</h2><p class="vm-sub">The pages visitors see.</p>'
            . ($installed ? '<p style="font-size:22px;margin:4px 0"><strong>' . number_format_i18n($pages) . '</strong> <span class="description">pages</span></p><p>' . self::link('vanmeya-website', 'Update website', 'button') . ' ' . '<a class="button button-primary" target="_blank" href="' . esc_url(home_url('/')) . '">Open the website</a></p>' : '<p>Upload the website file from GitHub to create all pages.</p>' . self::link('vanmeya-website', 'Install website', 'button button-primary')) . '</div>';
        echo '<div class="vm-card"><h2>SEO ' . ($hidden ? self::pill('Hidden from Google', 'warn') : self::pill('Visible to Google', 'ok')) . '</h2><p class="vm-sub">Titles, redirects, tracking and checks.</p>'
            . '<p>' . (!empty($seo['gsc']) ? self::pill('Search Console', 'ok') : self::pill('Search Console: not added', 'mute')) . ' ' . (!empty($seo['ga4']) || !empty($seo['gtm']) ? self::pill('Analytics', 'ok') : self::pill('Analytics: not added', 'mute')) . '</p>'
            . self::link('vanmeya-seo', 'Check my pages', 'button button-primary') . ' ' . self::link('vanmeya-seo', 'Set up tracking', 'button', ['tab' => 'tracking']) . '</div>';
        echo '</div>';
        echo '<div class="vm-card"><h2>How updates work</h2><p class="vm-sub">Whenever you add restaurants or change content, follow these four steps.</p><div class="vm-flow">'
            . '<div><strong>Update the Google Sheet</strong><br><span class="description">Add restaurants or approve content.</span></div>'
            . '<div><strong>Run the GitHub workflow</strong><br><span class="description">"photos" for pictures, "hosting-site" for pages.</span></div>'
            . '<div><strong>Upload the file here</strong><br><span class="description">Photos screen or Website screen.</span></div>'
            . '<div><strong>Edit and check</strong><br><span class="description">Pages &amp; content and SEO screens.</span></div></div></div>';
        echo '<div class="vm-card"><h2>Where do I do what?</h2><table class="vm-table"><tbody>'
            . '<tr><td style="width:180px">' . self::link('vanmeya-photos', 'Photos', 'button') . '</td><td>Upload the images file, download restaurant photos, fix problem photos.</td></tr>'
            . '<tr><td>' . self::link('vanmeya-website', 'Website', 'button') . '</td><td>Upload the website file. This creates or refreshes all pages.</td></tr>'
            . '<tr><td>' . self::link('vanmeya-pages', 'Pages & content', 'button') . '</td><td>Find any page, change its text, add extra content or a brand new page.</td></tr>'
            . '<tr><td>' . self::link('vanmeya-seo', 'SEO', 'button') . '</td><td>Problems check, bulk edit in Excel, title rules, redirects, tracking, robots.txt.</td></tr>'
            . '<tr><td>' . self::link('vanmeya-settings', 'Settings', 'button') . '</td><td>Hide or show the site to Google. Background downloads.</td></tr></tbody></table></div>';
        echo '<details class="vm-card"><summary style="margin:0;font-weight:600">Check this hosting</summary><table class="vm-table" style="margin-top:8px"><tbody>';
        foreach (Vanmeya_WP::check_rows() as $r) echo '<tr><td style="width:240px"><strong>' . esc_html($r[0]) . '</strong></td><td>' . esc_html($r[1]) . '</td><td style="width:30px">' . ($r[2] ? '&#10004;' : '&#9888;') . '</td></tr>';
        echo '</tbody></table></details>';
    }

    public static function photos($c, $st, $nonce) {
        $total = max(1, $c['done'] + $c['pending'] + $c['failed']); $problems = max(0, $c['failed'] - $c['skipped']);
        $w = function ($n) use ($total) { return round(100 * $n / $total, 2); };
        echo '<p class="vm-lead">Two steps: give the plugin the images file, then let it download the photos. Each restaurant gets its own folder.</p><div id="vm-live" class="vm-live"></div>';
        echo '<div class="vm-card"><h2>Step 1. Upload the images file</h2><p class="vm-sub">The <strong>images_database.csv</strong> from the GitHub "photos" run.</p>';
        echo '<form method="post" enctype="multipart/form-data" data-chunk="csv" action="' . esc_url(admin_url('admin-post.php')) . '">' . self::hidden('vanmeya_csv', $nonce) . '<div class="vm-file"><input type="file" name="csv" accept=".csv"> <button class="button button-primary">Upload and add to the list</button>';
        echo '<br><label style="display:block;margin-top:8px"><input type="checkbox" name="use_incoming" value="1"> My file is very large: I put it in <code>incoming/images_database.csv</code> with the hosting file manager</label></div></form></div>';
        echo '<div class="vm-card"><h2>Step 2. Download the photos</h2><p class="vm-sub">Press once and keep this page open. If your hosting cannot reach Google, your own browser takes over automatically.</p>';
        echo '<div class="vm-stats"><div class="vm-stat"><b id="vm-s-done">' . (int)$c['done'] . '</b><span><i class="vm-dot" style="background:#1a7f37"></i>Saved</span></div><div class="vm-stat"><b id="vm-s-pending">' . (int)$c['pending'] . '</b><span><i class="vm-dot" style="background:#2271b1"></i>Waiting</span></div><div class="vm-stat"><b id="vm-s-skipped">' . (int)$c['skipped'] . '</b><span><i class="vm-dot" style="background:#a7aaad"></i>Skipped (too small)</span></div><div class="vm-stat"><b id="vm-s-problem">' . (int)$problems . '</b><span><i class="vm-dot" style="background:#d63638"></i>Problems</span></div></div>';
        echo '<div class="vm-bar"><i id="vm-b-done" style="background:#1a7f37;width:' . $w($c['done']) . '%"></i><i id="vm-b-pending" style="background:#2271b1;width:' . $w($c['pending']) . '%"></i><i id="vm-b-skipped" style="background:#a7aaad;width:' . $w($c['skipped']) . '%"></i><i id="vm-b-problem" style="background:#d63638;width:' . $w($problems) . '%"></i></div>';
        echo '<p><button class="button button-primary vm-big" id="vm-go">Download photos</button> <button class="button vm-big" id="vm-pause" disabled>Pause</button> <span id="vm-note" class="description" style="margin-left:8px"></span></p></div>';
        if ($c['failed']) {
            echo '<div class="vm-card"><h2>Photos that did not download</h2><p class="vm-sub">Most of these are tiny pictures that are skipped on purpose.</p><table class="vm-table"><thead><tr><th>What happened</th><th style="width:70px">Photos</th><th>What to do</th></tr></thead><tbody>';
            $groups = [];
            foreach ($st->reasons() as $note => $n) { $r = self::reason($note === 'unknown' ? '' : $note); $groups[$r[0]] = ['n' => (isset($groups[$r[0]]) ? $groups[$r[0]]['n'] : 0) + $n, 'r' => $r]; }
            foreach ($groups as $label => $g) $dot = ['mute' => '#a7aaad', 'warn' => '#dba617', 'bad' => '#d63638'][$g['r'][2]]; echo '<tr><td><i class="vm-dot" style="background:' . $dot . '"></i>' . esc_html($label) . '</td><td><strong>' . number_format_i18n($g['n']) . '</strong></td><td>' . esc_html($g['r'][1]) . '</td></tr>';
            echo '</tbody></table><form method="post" action="' . esc_url(admin_url('admin-post.php')) . '" style="margin-top:12px">' . self::hidden('vanmeya_retry', $nonce) . '<button class="button button-primary">Retry problem photos</button> <span class="description">Small pictures are asked for again in a bigger size. Expired links are left alone.</span></form>';
            echo '<details><summary>Show some of the problem photos</summary><ul>';
            foreach ($st->failed_sample() as $f) echo '<li><code>' . esc_html($f['folder'] . ' #' . $f['idx']) . '</code> ' . esc_html(wp_html_excerpt($f['url'], 90, '...')) . '</li>';
            echo '</ul></details></div>';
        }
        echo '<details class="vm-card"><summary style="margin:0;font-weight:600">Advanced</summary><p><form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">' . self::hidden('vanmeya_test', $nonce) . '<button class="button">Test the hosting connection to Google</button> <span class="description">Shows exactly what happens when the hosting asks Google for a photo.</span></form></p>';
        echo '<p>Photos are saved in <code>' . esc_html(Vanmeya_WP::photo_base_url_path()) . '/&lt;place_id&gt;/1.webp</code>, 2.webp and so on.</p></details>';
    }

    public static function website($installed, $pages, $nonce) {
        echo '<p class="vm-lead">Upload the website file once. It creates every country, city, area and restaurant page.</p><div id="vm-live" class="vm-live"></div>';
        echo '<div class="vm-card"><h2>Status ' . ($installed ? self::pill('Installed', 'ok') : self::pill('Not installed yet', 'bad')) . '</h2>';
        if ($installed) echo '<p>' . ($pages ? '<strong>' . number_format_i18n($pages) . '</strong> pages. ' : '') . 'Last updated ' . esc_html(date_i18n('j M Y, H:i', filemtime(Vanmeya_WP::site_dir() . '/index.html'))) . '.</p><p><a class="button button-primary" target="_blank" href="' . esc_url(home_url('/')) . '">Open the website</a> ' . self::link('vanmeya-pages', 'Edit pages', 'button') . ' ' . self::link('vanmeya-seo', 'SEO', 'button') . '</p>';
        else echo '<p>Nothing is installed yet. Do the step below.</p>';
        echo '</div><div class="vm-card"><h2>Upload the website file</h2><p class="vm-sub">The <strong>site-for-hosting</strong> file from the GitHub "hosting-site" run. The zip you download from GitHub works as it is. The site builder needs this photo path: <code>' . esc_html(Vanmeya_WP::photo_base_url_path()) . '</code></p>';
        echo '<form method="post" enctype="multipart/form-data" data-chunk="zip" action="' . esc_url(admin_url('admin-post.php')) . '">' . self::hidden('vanmeya_zip', $nonce) . '<div class="vm-file"><input type="file" name="zip" accept=".zip"> <button class="button button-primary vm-big">Upload and install</button>';
        echo '<p class="description">Keep this page open. You will see the progress above: uploading, unpacking, done. Big files are no problem.</p>';
        echo '<label><input type="checkbox" name="use_incoming" value="1"> My file is already in <code>' . esc_html(Vanmeya_WP::incoming()) . '/site-for-hosting.zip</code></label></div></form></div>';
    }

    public static function settings($nonce) {
        echo '<p class="vm-lead">Two switches.</p><form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">' . self::hidden('vanmeya_settings', $nonce);
        echo '<div class="vm-card"><h2>Visibility to Google</h2><label><input type="checkbox" name="noindex" value="1" ' . checked(Vanmeya_WP::opt('noindex', 1), 1, false) . '> <strong>Hide this site from Google</strong></label><p class="description">Keep this ticked on a test (staging) site so Google does not list it. Untick it on the day you go live.</p></div>';
        echo '<div class="vm-card"><h2>Background photo downloads</h2><label><input type="checkbox" name="background" value="1" ' . checked(Vanmeya_WP::opt('background', 0), 1, false) . '> Keep downloading photos when this page is closed</label><p class="description">Needs visitors on the site or a server cron job. Most people leave this off.</p></div>';
        echo '<div class="vm-card"><h2>Smallest photo to keep</h2><p>Photos with a short side under <select name="min_side">';
        foreach ([100, 150, 200, 300] as $v) echo '<option value="' . $v . '"' . selected(Vanmeya_WP::min_side(), $v, false) . '>' . $v . ' pixels</option>';
        echo '</select> are skipped.</p><p class="description">200 keeps pages looking sharp. Lower it (150 or 100) if you would rather show every photo, even small ones. After changing it, press <strong>Retry problem photos</strong> on the Photos screen.</p></div>';
        echo '<p><button class="button button-primary vm-big">Save settings</button></p></form>';
    }

    /** All the browser code of the four screens. */
    public static function js($ajax, $nonce) {
        $js = <<<'JS'
(function(){
var AJ="__AJ__",NC="__NC__";
function $(i){return document.getElementById(i);}
function post(a,f){f=f||new FormData();f.append("action",a);f.append("_wpnonce",NC);return fetch(AJ,{method:"POST",body:f,credentials:"same-origin"}).then(function(r){return r.json();});}
function say(m,k){var l=$("vm-live");if(!l)return;l.textContent=m;l.className="vm-live"+(k?" "+k:"");l.style.display=m?"block":"none";}
function num(n){return Number(n).toLocaleString();}
/* ---- upload in small pieces, then queue or install ---- */
document.querySelectorAll("form[data-chunk]").forEach(function(fm){fm.addEventListener("submit",function(e){
 var inp=fm.querySelector("input[type=file]"),file=inp&&inp.files[0];if(!file||fm.dataset.sent)return;e.preventDefault();
 var kind=fm.dataset.chunk,size=1048576,total=Math.ceil(file.size/size),i=0,btn=fm.querySelector("button");btn.disabled=true;var oldTxt=btn.textContent;
 function fail(m){say(m,"bad");btn.disabled=false;btn.textContent="Try again";}
 function next(){
  if(i>=total){inp.value="";if(kind==="zip"){install();return;}fm.dataset.sent=1;fm.querySelector("input[name=use_incoming]").checked=true;btn.textContent="Reading the file...";say("Upload finished. Reading the file, please wait...");fm.submit();return;}
  var f=new FormData();f.append("kind",kind);f.append("index",i);f.append("piece",file.slice(i*size,(i+1)*size),"p");
  var pct=Math.round(100*i/total);btn.textContent="Uploading "+pct+"%";say("Uploading "+file.name+": "+pct+"%. Keep this page open.");
  post("vanmeya_chunk",f).then(function(j){if(!j.success)return fail("Upload stopped: "+j.data);i++;next();}).catch(function(){say("Connection hiccup, retrying...");setTimeout(next,2000);});}
 function install(){say("Upload finished. Checking the file...");var f=new FormData();f.append("phase","start");post("vanmeya_unzip",f).then(function(j){if(!j.success)return fail("Install stopped: "+j.data);step(0,j.data.total);}).catch(function(e){fail("No answer from the server ("+e+")");});}
 function step(pos,tot){btn.textContent="Unpacking "+Math.round(100*pos/tot)+"%";say("Unpacking website files: "+num(pos)+" of "+num(tot)+". Keep this page open.");
  if(pos>=tot){var g=new FormData();g.append("phase","finish");post("vanmeya_unzip",g).then(function(j){if(!j.success)return fail("Install stopped: "+j.data);say("Done. The website is installed with "+num(j.data.pages)+" pages.","good");btn.textContent="Installed";setTimeout(function(){location.href=location.pathname+"?page=vanmeya-website";},1800);}).catch(function(e){fail(""+e);});return;}
  var f=new FormData();f.append("phase","run");f.append("pos",pos);post("vanmeya_unzip",f).then(function(j){if(!j.success)return fail("Install stopped: "+j.data);step(j.data.pos,j.data.total);}).catch(function(){say("Connection hiccup, retrying...");setTimeout(function(){step(pos,tot);},2500);});}
 next();});});
/* ---- photos: server first, then your own browser ---- */
var go=$("vm-go");if(!go)return;var pause=$("vm-pause"),note=$("vm-note"),st={run:false,stuck:0,saved:0};
function paint(c){var t=(c.done+c.pending+c.failed)||1,pr=Math.max(0,c.failed-c.skipped);
 $("vm-s-done").textContent=num(c.done);$("vm-s-pending").textContent=num(c.pending);$("vm-s-skipped").textContent=num(c.skipped);$("vm-s-problem").textContent=num(pr);
 [["done",c.done],["pending",c.pending],["skipped",c.skipped],["problem",pr]].forEach(function(x){$("vm-b-"+x[0]).style.width=(100*x[1]/t)+"%";});}
function idle(){st.run=false;go.disabled=false;go.textContent="Download photos";pause.disabled=true;}
function stop(m,k){idle();say(m,k||"");}
function finish(c){idle();say("Finished. "+num(c.done)+" photos are saved. Reloading to show what is left...","good");setTimeout(function(){location.reload();},2200);}
function serverLoop(){if(!st.run)return;post("vanmeya_step").then(function(j){
  if(!j.success){stop("Stopped: "+(j.data||"error"),"bad");return;}
  var c=j.data.counts,s=j.data.step;paint(c);
  if(s.taken===0){browserPhase();return;}
  if(s.ok===0&&s.net>0)st.stuck++;else st.stuck=0;
  if(st.stuck>=3){say("Your hosting cannot reach Google directly, so your own browser takes over now. Keep this page open.");browserPhase();return;}
  say("Downloading with the server... "+num(c.done)+" saved, "+num(c.pending)+" waiting. Keep this page open.");setTimeout(serverLoop,200);
 }).catch(function(){say("Connection hiccup, trying again...");setTimeout(serverLoop,3000);});}
function big(u){if(!/googleusercontent\.com/.test(u))return u;var m=u.match(/^([^=\s]+)=[A-Za-z0-9\-_]+$/);if(m)return m[1]+"=s1200";return u.indexOf("=")<0?u+"=s1200":u;}
function grab(u){return fetch(u,{mode:"cors",referrerPolicy:"no-referrer"}).then(function(r){if(!r.ok)throw new Error("http_"+r.status);return r.blob();}).then(function(b){return createImageBitmap(b);});}
var MIN=__MIN__;
function wait(ms){return new Promise(function(r){setTimeout(r,ms);});}
function grabR(u,n){return grab(u).catch(function(e){if(n>=3||!/Failed to fetch|http_429|http_5/.test(e.message))throw e;return wait(1500*Math.pow(2,n)).then(function(){return grabR(u,n+1);});});}
function probe(u){return new Promise(function(ok){var i=new Image();i.referrerPolicy="no-referrer";i.onload=function(){ok("nocors");};i.onerror=function(){ok("gone");};i.src=u;});}
function webp(u){return grabR(u,0).then(function(bm){if((bm.width<MIN||bm.height<MIN)&&big(u)!==u)return grabR(big(u),0);return bm;}).then(function(bm){var w=bm.width,h=bm.height;if(w<MIN||h<MIN)throw new Error("too_small");var s=w>800?800/w:1,cv=document.createElement("canvas");cv.width=Math.round(w*s);cv.height=Math.round(h*s);cv.getContext("2d").drawImage(bm,0,0,cv.width,cv.height);return new Promise(function(ok,no){cv.toBlob(function(x){x?ok(x):no(new Error("encode"));},"image/webp",0.78);});});}
function one(it){return webp(it.url).then(function(b){var f=new FormData();f.append("id",it.id);f.append("img",b,"x.webp");return post("vanmeya_bput",f);}).catch(function(err){var f=new FormData();f.append("id",it.id);var m=(err&&err.message)||"error";if(m==="Failed to fetch"){return probe(it.url).then(function(k){f.append("fail",k);return post("vanmeya_bput",f);});}f.append("fail",m);return post("vanmeya_bput",f);});}
var par=8;
function browserPhase(){if(!st.run)return;var f=new FormData();f.append("n",par);post("vanmeya_btake",f).then(function(j){
  if(!j.success){stop("Stopped: "+(j.data||"error"),"bad");return;}
  paint(j.data.counts);if(!j.data.items.length){finish(j.data.counts);return;}
  Promise.all(j.data.items.map(one)).then(function(r){var l=r[r.length-1],c=l&&l.data&&l.data.counts;var bad=0;r.forEach(function(x){if(!x||!x.success)bad++;});
   if(c){paint(c);say("Downloading with your browser... "+num(c.done)+" saved, "+num(c.pending)+" waiting. Keep this page open.");}
   var fl=c?c.failed:0;if(st.lastFail!==undefined&&fl-st.lastFail>=Math.ceil(r.length/3)){par=Math.max(2,Math.floor(par/2));st.slow=true;}else if(par<12)par++;st.lastFail=fl;
   if(st.slow){st.slow=false;say("Google is slowing us down, pausing a few seconds...");wait(6000).then(browserPhase);}else browserPhase();});
 }).catch(function(){say("Connection hiccup, trying again...");setTimeout(browserPhase,3000);});}
go.onclick=function(){st.run=true;st.stuck=0;go.disabled=true;go.textContent="Working...";pause.disabled=false;say("Starting...");serverLoop();};
pause.onclick=function(){st.run=false;idle();say("Paused. Press Download photos to continue.");};
})();
JS;
        return str_replace(['__AJ__', '__NC__', '__MIN__'], [esc_url($ajax), esc_js($nonce), (int)Vanmeya_WP::min_side()], $js);
    }
}
