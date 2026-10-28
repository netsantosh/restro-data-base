<?php
if (!defined('ABSPATH')) { exit; }

/** Download queue stored in a WordPress database table. Same methods as Vanmeya_Store_Memory. */
class Vanmeya_Store_Wpdb {
    private $t;
    public function __construct() { global $wpdb; $this->t = $wpdb->prefix . 'vanmeya_queue'; }
    public function table() { return $this->t; }
    public function reset() { global $wpdb; $wpdb->query("TRUNCATE TABLE {$this->t}"); }
    public function add($folder, $idx, $url) {
        global $wpdb;
        $wpdb->query($wpdb->prepare(
            "INSERT INTO {$this->t} (folder, idx, url, status, tries) VALUES (%s, %d, %s, 'pending', 0)
             ON DUPLICATE KEY UPDATE status = IF(url = VALUES(url) AND status = 'done', 'done', 'pending'), tries = IF(url = VALUES(url) AND status = 'done', tries, 0), url = VALUES(url)",
            $folder, $idx, $url));
    }
    public function take($n, $max_tries) {
        global $wpdb;
        $rows = $wpdb->get_results($wpdb->prepare("SELECT id, folder, idx, url, tries FROM {$this->t} WHERE status = 'pending' AND tries < %d ORDER BY id LIMIT %d", $max_tries, $n), ARRAY_A);
        foreach ($rows as &$r) { $r['place_id'] = $r['folder']; $r['id'] = (int)$r['id']; $r['idx'] = (int)$r['idx']; $r['tries'] = (int)$r['tries']; }
        return $rows;
    }
    public function finish($id, $status, $bump) {
        global $wpdb;
        $wpdb->query($wpdb->prepare("UPDATE {$this->t} SET status = %s, tries = tries + %d WHERE id = %d", $status, $bump ? 1 : 0, $id));
    }
    public function counts() {
        global $wpdb;
        $c = ['pending' => 0, 'done' => 0, 'failed' => 0];
        foreach ($wpdb->get_results("SELECT status, COUNT(*) n FROM {$this->t} GROUP BY status", ARRAY_A) as $r) { $c[$r['status']] = (int)$r['n']; }
        return $c;
    }
    public function retry_failed() { global $wpdb; return $wpdb->query("UPDATE {$this->t} SET status = 'pending', tries = 0 WHERE status = 'failed'"); }
    public function failed_sample($n = 40) { global $wpdb; return $wpdb->get_results($wpdb->prepare("SELECT folder, idx, url FROM {$this->t} WHERE status = 'failed' ORDER BY id LIMIT %d", $n), ARRAY_A); }
}

class Vanmeya_WP {
    const CAP = 'manage_options';

    public static function init() {
        add_action('init', [__CLASS__, 'serve_site'], 0);
        add_action('template_redirect', [__CLASS__, 'serve_404'], 0);
        add_action('admin_menu', [__CLASS__, 'menu']);
        add_action('admin_post_vanmeya_csv', [__CLASS__, 'handle_csv']);
        add_action('admin_post_vanmeya_zip', [__CLASS__, 'handle_zip']);
        add_action('admin_post_vanmeya_settings', [__CLASS__, 'handle_settings']);
        add_action('admin_post_vanmeya_retry', [__CLASS__, 'handle_retry']);
        add_action('wp_ajax_vanmeya_step', [__CLASS__, 'ajax_step']);
        add_filter('cron_schedules', function ($s) { $s['vanmeya_minute'] = ['interval' => 60, 'display' => 'Every minute']; return $s; });
        add_action('vanmeya_cron', [__CLASS__, 'cron']);
    }

    /* ---------- paths ---------- */
    public static function base_dir() { $u = wp_upload_dir(); return trailingslashit($u['basedir']) . 'vanmeya'; }
    public static function photo_dir() { return self::base_dir() . '/photos'; }
    public static function site_dir() { return self::base_dir() . '/site'; }
    public static function incoming() { return self::base_dir() . '/incoming'; }
    public static function photo_base_url_path() { $u = wp_upload_dir(); return rtrim((string)wp_parse_url($u['baseurl'], PHP_URL_PATH), '/') . '/vanmeya/photos'; }
    public static function opt($k, $d = '') { $o = get_option('vanmeya_opts', []); return isset($o[$k]) ? $o[$k] : $d; }
    public static function set_opt($k, $v) { $o = get_option('vanmeya_opts', []); $o[$k] = $v; update_option('vanmeya_opts', $o, false); }
    public static function images() { return new Vanmeya_Images(self::photo_dir(), new Vanmeya_Store_Wpdb()); }

    /* ---------- activate ---------- */
    public static function activate() {
        global $wpdb;
        require_once ABSPATH . 'wp-admin/includes/upgrade.php';
        $t = $wpdb->prefix . 'vanmeya_queue';
        dbDelta("CREATE TABLE $t (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            folder VARCHAR(190) NOT NULL,
            idx SMALLINT UNSIGNED NOT NULL,
            url TEXT NOT NULL,
            status VARCHAR(10) NOT NULL DEFAULT 'pending',
            tries TINYINT UNSIGNED NOT NULL DEFAULT 0,
            PRIMARY KEY  (id),
            UNIQUE KEY photo (folder, idx),
            KEY status (status)
        ) " . $wpdb->get_charset_collate() . ";");
        foreach ([self::base_dir(), self::photo_dir(), self::incoming()] as $d) { wp_mkdir_p($d); }
        @file_put_contents(self::base_dir() . '/index.html', '');           // no folder listing
        @file_put_contents(self::incoming() . '/.htaccess', "Require all denied\nDeny from all\n");
        if (self::opt('noindex', null) === null) self::set_opt('noindex', 1); // staging stays hidden from Google until you switch it off
    }
    public static function deactivate() { wp_clear_scheduled_hook('vanmeya_cron'); }

    /* ---------- serving the built site ---------- */
    private static $mime = ['html' => 'text/html; charset=utf-8', 'css' => 'text/css; charset=utf-8', 'js' => 'application/javascript; charset=utf-8',
        'json' => 'application/json; charset=utf-8', 'xml' => 'application/xml; charset=utf-8', 'txt' => 'text/plain; charset=utf-8',
        'svg' => 'image/svg+xml', 'png' => 'image/png', 'jpg' => 'image/jpeg', 'jpeg' => 'image/jpeg', 'webp' => 'image/webp', 'ico' => 'image/x-icon',
        'woff2' => 'font/woff2', 'woff' => 'font/woff'];

    private static function request_path() {
        $p = rawurldecode((string)wp_parse_url(isset($_SERVER['REQUEST_URI']) ? $_SERVER['REQUEST_URI'] : '/', PHP_URL_PATH));
        $home = rtrim((string)wp_parse_url(home_url(), PHP_URL_PATH), '/');
        if ($home !== '' && strpos($p, $home) === 0) $p = substr($p, strlen($home));
        return $p === '' ? '/' : $p;
    }

    private static function send_headers($ctype, $path, $mtime, $html) {
        header('Content-Type: ' . $ctype);
        header('Cache-Control: public, max-age=' . ($html ? 300 : 604800));
        header('Last-Modified: ' . gmdate('D, d M Y H:i:s', $mtime) . ' GMT');
        if (self::opt('noindex', 1)) header('X-Robots-Tag: noindex, nofollow');
    }

    public static function serve_site() {
        if (is_admin() || (defined('DOING_AJAX') && DOING_AJAX) || (defined('REST_REQUEST') && REST_REQUEST)) return;
        if (!is_dir(self::site_dir()) || !is_file(self::site_dir() . '/index.html')) return;
        $p = self::request_path();
        if (strpos($p, '..') !== false || preg_match('#^/(wp-|xmlrpc)#', $p)) return;
        if ($p === '/robots.txt' && self::opt('noindex', 1)) {
            header('Content-Type: text/plain; charset=utf-8'); echo "User-agent: *\nDisallow: /\n"; exit;
        }
        $root = realpath(self::site_dir());
        $rel = ltrim($p, '/');
        $file = $root . '/' . $rel;
        if ($rel === '' || substr($p, -1) === '/') $file = rtrim($file, '/') . '/index.html';
        elseif (is_dir($file)) { wp_redirect(home_url($p . '/'), 301); exit; }
        $real = realpath($file);
        if (!$real || strpos($real, $root . DIRECTORY_SEPARATOR) !== 0 || !is_file($real)) return;   // not ours: WordPress carries on
        $ext = strtolower(pathinfo($real, PATHINFO_EXTENSION));
        $ctype = isset(self::$mime[$ext]) ? self::$mime[$ext] : 'application/octet-stream';
        $mtime = filemtime($real);
        if (isset($_SERVER['HTTP_IF_MODIFIED_SINCE']) && strtotime($_SERVER['HTTP_IF_MODIFIED_SINCE']) >= $mtime) { status_header(304); exit; }
        self::send_headers($ctype, $real, $mtime, $ext === 'html');
        header('Content-Length: ' . filesize($real));
        readfile($real);
        exit;
    }

    public static function serve_404() {
        if (!is_404()) return;
        $f = self::site_dir() . '/404.html';
        if (!is_file($f)) return;
        status_header(404);
        self::send_headers(self::$mime['html'], $f, filemtime($f), true);
        readfile($f);
        exit;
    }

    /* ---------- admin screen ---------- */
    public static function menu() {
        add_menu_page('Vanmeya', 'Vanmeya', self::CAP, 'vanmeya', [__CLASS__, 'page'], 'dashicons-store', 58);
    }

    private static function check_rows() {
        $up = wp_upload_dir();
        $free = @disk_free_space($up['basedir']);
        $rows = [
            ['PHP version', PHP_VERSION, version_compare(PHP_VERSION, '7.4', '>=')],
            ['Image tools (WebP)', Vanmeya_Images::can_make_webp() ? 'ready' : 'missing: ask your host to enable the GD extension with WebP', Vanmeya_Images::can_make_webp()],
            ['Parallel downloads (curl)', function_exists('curl_multi_init') ? 'ready' : 'missing', function_exists('curl_multi_init')],
            ['Zip support', class_exists('ZipArchive') ? 'ready' : 'missing', class_exists('ZipArchive')],
            ['Memory limit', ini_get('memory_limit'), true],
            ['Largest upload', ini_get('upload_max_filesize') . ' (bigger files can be placed in the incoming folder, see below)', true],
            ['Free disk space', $free ? size_format($free) : 'unknown', !$free || $free > 500 * 1048576],
            ['Photo folder writable', wp_is_writable(self::base_dir()) || wp_mkdir_p(self::photo_dir()) ? 'yes' : 'no', wp_is_writable(self::base_dir())],
        ];
        return $rows;
    }

    public static function page() {
        if (!current_user_can(self::CAP)) return;
        $st = new Vanmeya_Store_Wpdb();
        $c = $st->counts();
        $total = max(1, array_sum($c));
        $nonce = wp_create_nonce('vanmeya');
        $installed = is_file(self::site_dir() . '/index.html');
        $msg = isset($_GET['vm']) ? sanitize_text_field(wp_unslash($_GET['vm'])) : '';
        echo '<div class="wrap"><h1>Vanmeya Restaurant Guide</h1>';
        if ($msg) echo '<div class="notice notice-info"><p>' . esc_html($msg) . '</p></div>';

        echo '<h2>1. Check this hosting</h2><table class="widefat striped" style="max-width:820px"><tbody>';
        foreach (self::check_rows() as $r) echo '<tr><td style="width:240px"><strong>' . esc_html($r[0]) . '</strong></td><td>' . esc_html($r[1]) . '</td><td style="width:30px">' . ($r[2] ? '&#10004;' : '&#9888;') . '</td></tr>';
        echo '</tbody></table>';

        echo '<h2>2. Photos</h2><p>Upload the <strong>images_database.csv</strong> file from the GitHub run. The plugin downloads each restaurant\'s photos into its own folder: <code>' . esc_html(self::photo_base_url_path()) . '/&lt;place_id&gt;/1.webp</code>, 2.webp and so on.</p>';
        echo '<form method="post" enctype="multipart/form-data" action="' . esc_url(admin_url('admin-post.php')) . '">';
        echo '<input type="hidden" name="action" value="vanmeya_csv"><input type="hidden" name="_wpnonce" value="' . esc_attr($nonce) . '">';
        echo '<input type="file" name="csv" accept=".csv"> <button class="button button-primary">Upload and queue photos</button> ';
        echo '<label style="margin-left:12px"><input type="checkbox" name="use_incoming" value="1"> use <code>incoming/images_database.csv</code> instead (for very large files)</label></form>';

        echo '<div style="max-width:820px;margin:14px 0"><div style="background:#e5e5e5;height:18px;border-radius:9px;overflow:hidden"><div id="vm-bar" style="background:#1d2327;height:100%;width:' . esc_attr(round(100 * $c['done'] / $total)) . '%"></div></div>';
        echo '<p id="vm-text">Downloaded <strong id="vm-done">' . (int)$c['done'] . '</strong> &middot; waiting <strong id="vm-pending">' . (int)$c['pending'] . '</strong> &middot; failed <strong id="vm-failed">' . (int)$c['failed'] . '</strong></p>';
        echo '<button class="button button-primary" id="vm-start">Start downloading</button> <button class="button" id="vm-stop" disabled>Pause</button> ';
        echo '<span id="vm-note" style="margin-left:10px;color:#555">Keep this page open while it runs. You can pause and continue at any time.</span></div>';
        if ($c['failed']) {
            echo '<form method="post" action="' . esc_url(admin_url('admin-post.php')) . '"><input type="hidden" name="action" value="vanmeya_retry"><input type="hidden" name="_wpnonce" value="' . esc_attr($nonce) . '"><button class="button">Try the ' . (int)$c['failed'] . ' failed photos again</button> <span class="description">Failed photos are usually links that expired. Upload a newer images database to replace them.</span></form>';
            echo '<details style="margin:10px 0"><summary>Show some failed photos</summary><ul>';
            foreach ($st->failed_sample() as $f) echo '<li><code>' . esc_html($f['folder'] . ' #' . $f['idx']) . '</code> ' . esc_html(wp_html_excerpt($f['url'], 90, '...')) . '</li>';
            echo '</ul></details>';
        }

        echo '<h2>3. Website pages</h2><p>Upload <strong>site-for-hosting.zip</strong> from the "hosting-site" GitHub run. When it is installed, the plugin shows the guide at the root of this site (<code>' . esc_html(home_url('/')) . '</code>).</p>';
        echo '<p>The site builder needs this photo path: <code>' . esc_html(self::photo_base_url_path()) . '</code></p>';
        echo '<p>' . ($installed ? '<strong>Installed.</strong> Last updated ' . esc_html(date_i18n('j M Y, H:i', filemtime(self::site_dir() . '/index.html'))) . '.' : 'Not installed yet.') . '</p>';
        echo '<form method="post" enctype="multipart/form-data" action="' . esc_url(admin_url('admin-post.php')) . '">';
        echo '<input type="hidden" name="action" value="vanmeya_zip"><input type="hidden" name="_wpnonce" value="' . esc_attr($nonce) . '">';
        echo '<input type="file" name="zip" accept=".zip"> <button class="button button-primary">Upload and install</button> ';
        echo '<label style="margin-left:12px"><input type="checkbox" name="use_incoming" value="1"> use <code>incoming/site-for-hosting.zip</code> instead</label></form>';
        echo '<p class="description">Large files: put them in <code>' . esc_html(self::incoming()) . '</code> with your hosting file manager, then tick the box.</p>';

        echo '<h2>4. Settings</h2><form method="post" action="' . esc_url(admin_url('admin-post.php')) . '"><input type="hidden" name="action" value="vanmeya_settings"><input type="hidden" name="_wpnonce" value="' . esc_attr($nonce) . '">';
        echo '<p><label><input type="checkbox" name="noindex" value="1" ' . checked(self::opt('noindex', 1), 1, false) . '> <strong>Hide this site from Google</strong> (keep ticked on a staging site; untick when you go live)</label></p>';
        echo '<p><label><input type="checkbox" name="background" value="1" ' . checked(self::opt('background', 0), 1, false) . '> Keep downloading in the background when this page is closed (needs site visits or a server cron job)</label></p>';
        echo '<p><button class="button">Save settings</button></p></form>';

        $ajax = esc_url(admin_url('admin-ajax.php'));
        echo '<script>(function(){var run=false,btn=document.getElementById("vm-start"),stop=document.getElementById("vm-stop"),note=document.getElementById("vm-note");
        function upd(c){var t=c.done+c.pending+c.failed||1;document.getElementById("vm-done").textContent=c.done;document.getElementById("vm-pending").textContent=c.pending;document.getElementById("vm-failed").textContent=c.failed;document.getElementById("vm-bar").style.width=Math.round(100*c.done/t)+"%";}
        function loop(){if(!run)return;var f=new FormData();f.append("action","vanmeya_step");f.append("_wpnonce","' . esc_js($nonce) . '");
        fetch("' . $ajax . '",{method:"POST",body:f,credentials:"same-origin"}).then(function(r){return r.json();}).then(function(j){if(!j.success){note.textContent="Stopped: "+(j.data||"error");run=false;btn.disabled=false;stop.disabled=true;return;}
        upd(j.data.counts);if(j.data.counts.pending===0){note.textContent="Finished.";run=false;btn.disabled=false;stop.disabled=true;return;}note.textContent="Working...";setTimeout(loop,200);}).catch(function(){note.textContent="Connection problem, trying again...";setTimeout(loop,3000);});}
        btn.onclick=function(){run=true;btn.disabled=true;stop.disabled=false;loop();};stop.onclick=function(){run=false;btn.disabled=false;stop.disabled=true;note.textContent="Paused.";};})();</script>';
        echo '</div>';
    }

    /* ---------- actions ---------- */
    private static function back($msg) { wp_safe_redirect(add_query_arg(['page' => 'vanmeya', 'vm' => rawurlencode($msg)], admin_url('admin.php'))); exit; }
    private static function guard() { if (!current_user_can(self::CAP) || !wp_verify_nonce(isset($_POST['_wpnonce']) ? $_POST['_wpnonce'] : '', 'vanmeya')) wp_die('Not allowed.'); }

    private static function incoming_or_upload($field, $fixed_name) {
        if (!empty($_POST['use_incoming'])) {
            $p = self::incoming() . '/' . $fixed_name;
            return is_file($p) ? $p : null;
        }
        if (!empty($_FILES[$field]['tmp_name']) && is_uploaded_file($_FILES[$field]['tmp_name']) && $_FILES[$field]['error'] === UPLOAD_ERR_OK) return $_FILES[$field]['tmp_name'];
        return null;
    }

    public static function handle_csv() {
        self::guard();
        wp_mkdir_p(self::photo_dir());
        $path = self::incoming_or_upload('csv', 'images_database.csv');
        if (!$path) self::back('No file received. If the file is large, use the incoming folder option.');
        @set_time_limit(300);
        list($rows, $err) = Vanmeya_Csv::read($path, 10);
        if ($err) self::back($err);
        $n = self::images()->enqueue($rows);
        if ($n === 0 && count($rows) > 0) self::back('Read ' . count($rows) . ' restaurants. Every photo in this file is already downloaded.');
        self::back('Read ' . count($rows) . ' restaurants and queued ' . number_format_i18n($n) . ' photos. Press "Start downloading".');
    }

    public static function handle_retry() { self::guard(); $n = (new Vanmeya_Store_Wpdb())->retry_failed(); self::back("Queued $n photos again."); }

    public static function handle_settings() {
        self::guard();
        self::set_opt('noindex', empty($_POST['noindex']) ? 0 : 1);
        self::set_opt('background', empty($_POST['background']) ? 0 : 1);
        if (self::opt('background', 0)) { if (!wp_next_scheduled('vanmeya_cron')) wp_schedule_event(time() + 60, 'vanmeya_minute', 'vanmeya_cron'); }
        else wp_clear_scheduled_hook('vanmeya_cron');
        self::back('Settings saved.');
    }

    public static function ajax_step() {
        if (!current_user_can(self::CAP) || !wp_verify_nonce(isset($_POST['_wpnonce']) ? $_POST['_wpnonce'] : '', 'vanmeya')) wp_send_json_error('Not allowed.');
        if (!Vanmeya_Images::can_make_webp()) wp_send_json_error('This hosting cannot create WebP images (GD with WebP is missing).');
        $res = self::run_for(15);
        wp_send_json_success(['counts' => (new Vanmeya_Store_Wpdb())->counts(), 'step' => $res]);
    }

    private static function run_for($seconds) {
        @set_time_limit($seconds + 30);
        $im = self::images();
        $t0 = time(); $tot = ['ok' => 0, 'failed' => 0, 'retry' => 0, 'taken' => 0];
        do {
            $r = $im->step(8);
            foreach ($tot as $k => $_) $tot[$k] += $r[$k];
        } while ($r['taken'] > 0 && time() - $t0 < $seconds);
        return $tot;
    }

    public static function cron() {
        if (!self::opt('background', 0) || !Vanmeya_Images::can_make_webp()) return;
        $c = (new Vanmeya_Store_Wpdb())->counts();
        if ($c['pending'] > 0) self::run_for(25);
    }

    /* ---------- install the site zip ---------- */
    public static function handle_zip() {
        self::guard();
        if (!class_exists('ZipArchive')) self::back('This hosting has no zip support.');
        $path = self::incoming_or_upload('zip', 'site-for-hosting.zip');
        if (!$path) self::back('No file received. If the file is large, use the incoming folder option.');
        @set_time_limit(300);
        $zip = new ZipArchive();
        if ($zip->open($path) !== true) self::back('That file is not a valid zip.');
        $names = [];
        for ($i = 0; $i < $zip->numFiles; $i++) {
            $n = $zip->getNameIndex($i);
            if ($n === false || strpos($n, '..') !== false || $n[0] === '/' || strpos($n, "\0") !== false || strpos($n, ':') !== false) { $zip->close(); self::back('The zip contains an unsafe path and was not installed.'); }
            $names[] = $n;
        }
        if (!in_array('index.html', $names, true)) { $zip->close(); self::back('This zip has no index.html at its top level. Use site-for-hosting.zip from the hosting-site run.'); }
        $new = self::base_dir() . '/site_new'; $old = self::base_dir() . '/site_old'; $cur = self::site_dir();
        self::rrmdir($new); self::rrmdir($old);
        wp_mkdir_p($new);
        if (!$zip->extractTo($new)) { $zip->close(); self::rrmdir($new); self::back('The zip could not be unpacked (disk full?).'); }
        $zip->close();
        if (is_dir($cur)) rename($cur, $old);
        rename($new, $cur);
        self::rrmdir($old);
        self::back('Website installed: ' . number_format_i18n(count($names)) . ' files.');
    }

    private static function rrmdir($d) {
        if (!is_dir($d)) return;
        foreach (new RecursiveIteratorIterator(new RecursiveDirectoryIterator($d, FilesystemIterator::SKIP_DOTS), RecursiveIteratorIterator::CHILD_FIRST) as $f) {
            $f->isDir() ? @rmdir($f->getPathname()) : @unlink($f->getPathname());
        }
        @rmdir($d);
    }
}
