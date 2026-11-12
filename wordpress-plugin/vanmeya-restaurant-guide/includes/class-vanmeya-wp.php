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
             ON DUPLICATE KEY UPDATE status = IF(url = VALUES(url) AND status = 'done', 'done', 'pending'), tries = IF(url = VALUES(url) AND status = 'done', tries, 0), alt = IF(url = VALUES(url), alt, ''), url = VALUES(url)",
            $folder, $idx, $url));
    }
    public function take($n, $max_tries) {
        global $wpdb;
        $rows = $wpdb->get_results($wpdb->prepare("SELECT id, folder, idx, COALESCE(NULLIF(alt, ''), url) AS url, tries FROM {$this->t} WHERE status = 'pending' AND tries < %d ORDER BY tries, id LIMIT %d", $max_tries, $n), ARRAY_A);
        foreach ($rows as &$r) { $r['place_id'] = $r['folder']; $r['id'] = (int)$r['id']; $r['idx'] = (int)$r['idx']; $r['tries'] = (int)$r['tries']; }
        return $rows;
    }
    public function finish($id, $status, $bump, $note = '') {
        global $wpdb;
        $wpdb->query($wpdb->prepare("UPDATE {$this->t} SET status = %s, tries = tries + %d, note = %s WHERE id = %d", $status, $bump ? 1 : 0, $note, $id));
    }
    public function reasons() {
        global $wpdb;
        $out = [];
        foreach ($wpdb->get_results("SELECT note, COUNT(*) n FROM {$this->t} WHERE status = 'failed' GROUP BY note ORDER BY n DESC", ARRAY_A) as $r) { $out[$r['note'] === '' ? 'unknown' : $r['note']] = (int)$r['n']; }
        return $out;
    }
    public function counts() {
        global $wpdb;
        $c = ['pending' => 0, 'done' => 0, 'failed' => 0];
        foreach ($wpdb->get_results("SELECT status, COUNT(*) n FROM {$this->t} GROUP BY status", ARRAY_A) as $r) { $c[$r['status']] = (int)$r['n']; }
        return $c;
    }
    /** Put problem photos back in the queue. Tiny ones get a bigger link; expired ones (404) are left alone. */
    public function retry_failed() {
        global $wpdb; $n = 0; $plain = [];
        foreach ($wpdb->get_results("SELECT id, url, alt, note FROM {$this->t} WHERE status = 'failed'", ARRAY_A) as $r) {
            $note = (string)$r['note'];
            if (strpos($note, 'http_404') !== false || strpos($note, 'http_410') !== false) continue;
            if (strpos($note, 'too_small') !== false || strpos($note, 'not_an_image') !== false) {
                $cur = $r['alt'] !== '' ? $r['alt'] : $r['url']; $big = Vanmeya_Images::enlarge($cur);
                if ($big === $cur && Vanmeya_WP::min_side() >= 200) continue;   // nothing bigger exists: truly too small
                $wpdb->update($this->t, ['alt' => $big, 'status' => 'pending', 'tries' => 0, 'note' => ''], ['id' => (int)$r['id']]); $n++;
            } else { $plain[] = (int)$r['id']; }
        }
        foreach (array_chunk($plain, 500) as $ids) { $n += (int)$wpdb->query("UPDATE {$this->t} SET status = 'pending', tries = 0, note = '' WHERE id IN (" . implode(',', $ids) . ")"); }
        return $n;
    }
    public function set_alt($id, $alt) { global $wpdb; $wpdb->update($this->t, ['alt' => $alt], ['id' => $id]); }
    public function skipped() { global $wpdb; return (int)$wpdb->get_var("SELECT COUNT(*) FROM {$this->t} WHERE status = 'failed' AND (note LIKE '%too_small%' OR note LIKE '%not_an_image%')"); }
    public function get($id) { global $wpdb; $r = $wpdb->get_row($wpdb->prepare("SELECT id, folder, idx, COALESCE(NULLIF(alt, ''), url) AS url, tries FROM {$this->t} WHERE id = %d", $id), ARRAY_A); if ($r) { $r['place_id'] = $r['folder']; $r['id'] = (int)$r['id']; $r['idx'] = (int)$r['idx']; } return $r; }
    public function browser_take($n) { global $wpdb; return $wpdb->get_results($wpdb->prepare("SELECT id, COALESCE(NULLIF(alt, ''), url) AS url FROM {$this->t} WHERE status IN ('pending','failed') AND note NOT LIKE 'browser%%' ORDER BY (status = 'pending') DESC, id LIMIT %d", $n), ARRAY_A); }
    public function failed_sample($n = 40) { global $wpdb; return $wpdb->get_results($wpdb->prepare("SELECT folder, idx, url FROM {$this->t} WHERE status = 'failed' ORDER BY id LIMIT %d", $n), ARRAY_A); }
}

class Vanmeya_WP {
    const CAP = 'manage_options';

    public static function init() {
        if (get_option('vanmeya_db') !== '5') { add_action('init', function () { self::activate(); update_option('vanmeya_db', '5', false); }, 1); }
        Vanmeya_Editor_Admin::init();
        Vanmeya_Seo_Admin::init();
        add_action('init', [__CLASS__, 'serve_site'], 0);
        add_action('template_redirect', [__CLASS__, 'serve_404'], 0);
        add_action('admin_menu', [__CLASS__, 'menu']);
        add_action('admin_head', [__CLASS__, 'admin_css']);
        add_action('admin_post_vanmeya_csv', [__CLASS__, 'handle_csv']);
        add_action('admin_post_vanmeya_zip', [__CLASS__, 'handle_zip']);
        add_action('admin_post_vanmeya_settings', [__CLASS__, 'handle_settings']);
        add_action('admin_post_vanmeya_test', [__CLASS__, 'handle_test']);
        add_action('admin_post_vanmeya_retry', [__CLASS__, 'handle_retry']);
        add_action('wp_ajax_vanmeya_step', [__CLASS__, 'ajax_step']);
        add_action('wp_ajax_vanmeya_chunk', [__CLASS__, 'ajax_chunk']);
        add_action('wp_ajax_vanmeya_unzip', [__CLASS__, 'ajax_unzip']);
        add_action('wp_ajax_vanmeya_btake', [__CLASS__, 'ajax_btake']);
        add_action('wp_ajax_vanmeya_bput', [__CLASS__, 'ajax_bput']);
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
    public static function min_side() { return max(80, min(600, (int)self::opt('min_side', 200))); }
    public static function images() { $im = new Vanmeya_Images(self::photo_dir(), new Vanmeya_Store_Wpdb()); $im->min_side = self::min_side(); return $im; }

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
            note VARCHAR(40) NOT NULL DEFAULT '',
            alt TEXT NULL,
            PRIMARY KEY  (id),
            UNIQUE KEY photo (folder, idx),
            KEY status (status)
        ) " . $wpdb->get_charset_collate() . ";");
        Vanmeya_Editor_Admin::install();
        Vanmeya_Seo_Admin::install();
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
        $key0 = trim($p, '/');
        if ($key0 !== '' && class_exists('Vanmeya_Seo_Admin')) {
            $rd = Vanmeya_Seo_Admin::redirect_for($key0);
            if ($rd) { wp_redirect(preg_match('#^https?://#i', $rd['target']) ? $rd['target'] : home_url($rd['target']), (int)$rd['code']); exit; }
        }
        if ($p === '/robots.txt') {
            header('Content-Type: text/plain; charset=utf-8');
            $seo = Vanmeya_Seo_Admin::settings();
            if (self::opt('noindex', 1)) echo "User-agent: *\nDisallow: /\n";
            elseif (!empty($seo['robots'])) echo $seo['robots'];
            else echo "User-agent: *\nAllow: /\n\nSitemap: " . home_url('/sitemap.xml') . "\n";
            exit;
        }
        if (preg_match('#^/sitemap(-\d+)?\.xml$#', $p, $sm) && is_file(self::site_dir() . $p)) {
            global $wpdb; $drop = []; $add = [];
            foreach ($wpdb->get_results("SELECT path, data, custom FROM " . Vanmeya_Editor_Admin::table(), ARRAY_A) as $r) {
                $d = json_decode($r['data'], true); if (!is_array($d)) continue;
                if (!empty($d['noindex'])) $drop[] = $r['path'];
                elseif ((int)$r['custom']) $add[] = home_url('/' . $r['path'] . '/');
            }
            header('Content-Type: application/xml; charset=utf-8');
            if (self::opt('noindex', 1)) header('X-Robots-Tag: noindex');
            $xml = (string)file_get_contents(self::site_dir() . $p);
            if (strpos($xml, '<sitemapindex') !== false) { echo $xml; exit; }   // the index lists the chunk files, nothing to filter
            echo Vanmeya_Seo_Core::filter_sitemap($xml, $drop, empty($sm[1]) || $sm[1] === '-1' ? $add : []); exit;
        }
        $root = realpath(self::site_dir());
        $rel = ltrim($p, '/');
        $file = $root . '/' . $rel;
        if ($rel === '' || substr($p, -1) === '/') $file = rtrim($file, '/') . '/index.html';
        elseif (is_dir($file)) { wp_redirect(home_url($p . '/'), 301); exit; }
        if ($rel === 'vm-pages.json' || $rel === '_vm_shell.html') return;
        $real = realpath($file);
        $key = trim($p, '/');
        if (!$real || strpos($real, $root . DIRECTORY_SEPARATOR) !== 0 || !is_file($real)) {
            $c = class_exists('Vanmeya_Editor_Admin') && $key !== '' ? Vanmeya_Editor_Admin::get($key) : null;     // a page added in the admin
            if ($c && $c['custom'] && is_file($root . '/_vm_shell.html')) {
                status_header(200);
                self::send_headers(self::$mime['html'], $root . '/_vm_shell.html', time(), true);
                header('Cache-Control: no-cache');
                echo Vanmeya_Seo_Core::decorate(Vanmeya_Editor::from_shell((string)file_get_contents($root . '/_vm_shell.html'), $c['data'], home_url('/' . $key . '/')), Vanmeya_Seo_Admin::settings(), $c['data']);
                exit;
            }
            return;   // not ours: WordPress carries on
        }
        $ext = strtolower(pathinfo($real, PATHINFO_EXTENSION));
        $ctype = isset(self::$mime[$ext]) ? self::$mime[$ext] : 'application/octet-stream';
        $mtime = filemtime($real);
        if ($ext === 'html' && class_exists('Vanmeya_Editor_Admin')) {
            $relf = substr($real, strlen($root) + 1);
            $pk = $relf === 'index.html' ? '' : (basename($relf) === 'index.html' ? str_replace(DIRECTORY_SEPARATOR, '/', dirname($relf)) : null);
            if ($pk !== null) {
                $c = Vanmeya_Editor_Admin::get($pk);
                $html = (string)file_get_contents($real);
                if ($c) $html = Vanmeya_Editor::apply($html, $c['data']);
                $html = Vanmeya_Seo_Core::decorate($html, Vanmeya_Seo_Admin::settings(), $c ? $c['data'] : []);
                self::send_headers($ctype, $real, $mtime, true);
                header('Cache-Control: no-cache');
                header_remove('Last-Modified');
                echo $html;
                exit;
            }
        }
        if (isset($_SERVER['HTTP_IF_MODIFIED_SINCE']) && strtotime($_SERVER['HTTP_IF_MODIFIED_SINCE']) >= $mtime) { status_header(304); exit; }
        self::send_headers($ctype, $real, $mtime, $ext === 'html');
        header('Content-Length: ' . filesize($real));
        readfile($real);
        exit;
    }

    public static function serve_404() {
        if (!is_404()) return;
        if (is_file(self::site_dir() . '/index.html') && class_exists('Vanmeya_Seo_Admin')) Vanmeya_Seo_Admin::log_404(trim(self::request_path(), '/'));
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
        add_submenu_page('vanmeya', 'Start here', 'Start here', self::CAP, 'vanmeya', [__CLASS__, 'page']);
        add_submenu_page('vanmeya', 'Photos', 'Photos', self::CAP, 'vanmeya-photos', [__CLASS__, 'page']);
        add_submenu_page('vanmeya', 'Website', 'Website', self::CAP, 'vanmeya-website', [__CLASS__, 'page']);
        add_action('admin_menu', function () { add_submenu_page('vanmeya', 'Settings', 'Settings', self::CAP, 'vanmeya-settings', [__CLASS__, 'page']); }, 99);
    }

    public static function check_rows() {
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
        $c = $st->counts(); $c['skipped'] = $st->skipped();
        $nonce = wp_create_nonce('vanmeya');
        $installed = is_file(self::site_dir() . '/index.html');
        $pg = isset($_GET['page']) ? sanitize_key($_GET['page']) : 'vanmeya';
        $sec = ['vanmeya-photos' => 'photos', 'vanmeya-website' => 'site', 'vanmeya-settings' => 'settings'][$pg] ?? 'dash';
        $titles = ['dash' => 'Vanmeya', 'photos' => 'Photos', 'site' => 'Website', 'settings' => 'Settings'];
        echo '<div class="wrap vm"><h1>' . esc_html($titles[$sec]) . '</h1>';
        if (!empty($_GET['vm'])) echo '<div class="notice notice-info"><p>' . esc_html(sanitize_text_field(wp_unslash($_GET['vm']))) . '</p></div>';
        if ($sec === 'dash') Vanmeya_UI::dashboard($c, $installed, (int)self::opt('html_pages', 0));
        elseif ($sec === 'photos') Vanmeya_UI::photos($c, $st, $nonce);
        elseif ($sec === 'site') Vanmeya_UI::website($installed, (int)self::opt('html_pages', 0), $nonce);
        else Vanmeya_UI::settings($nonce);
        echo '<script>' . Vanmeya_UI::js(admin_url('admin-ajax.php'), $nonce) . '</script></div>';
    }

    public static function admin_css() {
        $p = isset($_GET['page']) ? sanitize_key($_GET['page']) : '';
        if (strpos($p, 'vanmeya') === 0) echo Vanmeya_UI::css();
    }

    /* ---------- actions ---------- */
    private static function back($msg) {
        $to = ['vanmeya_csv' => 'vanmeya-photos', 'vanmeya_retry' => 'vanmeya-photos', 'vanmeya_test' => 'vanmeya-photos', 'vanmeya_zip' => 'vanmeya-website', 'vanmeya_settings' => 'vanmeya-settings'];
        $act = isset($_POST['action']) ? $_POST['action'] : '';
        wp_safe_redirect(add_query_arg(['page' => isset($to[$act]) ? $to[$act] : 'vanmeya', 'vm' => rawurlencode($msg)], admin_url('admin.php'))); exit;
    }
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

    public static function handle_test() {
        self::guard();
        global $wpdb; $t = $wpdb->prefix . 'vanmeya_queue';
        $url = $wpdb->get_var("SELECT url FROM $t ORDER BY (status = 'pending') DESC, id DESC LIMIT 1");
        $out = $url ? 'Photo link: ' . Vanmeya_Images::diagnose($url) : 'No photo links yet, upload the images database first.';
        $out .= ' | Google home page: ' . Vanmeya_Images::diagnose('https://www.google.com/');
        $out .= ' | Another image host: ' . Vanmeya_Images::diagnose('https://www.gstatic.com/images/branding/product/1x/googleg_48dp.png');
        self::back($out);
    }
    public static function handle_retry() { self::guard(); $n = (new Vanmeya_Store_Wpdb())->retry_failed(); self::back("Queued $n photos again."); }

    public static function handle_settings() {
        self::guard();
        self::set_opt('noindex', empty($_POST['noindex']) ? 0 : 1);
        self::set_opt('background', empty($_POST['background']) ? 0 : 1);
        $ms = isset($_POST['min_side']) ? (int)$_POST['min_side'] : 200; self::set_opt('min_side', max(80, min(600, $ms)));
        if (self::opt('background', 0)) { if (!wp_next_scheduled('vanmeya_cron')) wp_schedule_event(time() + 60, 'vanmeya_minute', 'vanmeya_cron'); }
        else wp_clear_scheduled_hook('vanmeya_cron');
        self::back('Settings saved.');
    }

    public static function ajax_step() {
        if (!current_user_can(self::CAP) || !wp_verify_nonce(isset($_POST['_wpnonce']) ? $_POST['_wpnonce'] : '', 'vanmeya')) wp_send_json_error('Not allowed.');
        if (!Vanmeya_Images::can_make_webp()) wp_send_json_error('This hosting cannot create WebP images (GD with WebP is missing).');
        $res = self::run_for(15);
        wp_send_json_success(['counts' => self::ui_counts(), 'step' => $res]);
    }

    /** Browser upload in small pieces, so the hosting's upload size limit does not matter. */
    public static function ajax_chunk() {
        if (!current_user_can(self::CAP) || !wp_verify_nonce(isset($_POST['_wpnonce']) ? $_POST['_wpnonce'] : '', 'vanmeya')) wp_send_json_error('Not allowed.');
        $kind = isset($_POST['kind']) ? $_POST['kind'] : '';
        $names = ['csv' => 'images_database.csv', 'zip' => 'site-for-hosting.zip'];
        if (!isset($names[$kind])) wp_send_json_error('Unknown file type.');
        if (empty($_FILES['piece']['tmp_name']) || !is_uploaded_file($_FILES['piece']['tmp_name'])) wp_send_json_error('The piece did not arrive.');
        $dir = self::incoming(); wp_mkdir_p($dir);
        $dest = $dir . '/' . $names[$kind];
        $i = (int)$_POST['index'];
        $data = file_get_contents($_FILES['piece']['tmp_name']);
        if (@file_put_contents($dest, $data, $i === 0 ? 0 : FILE_APPEND) === false) wp_send_json_error('Could not write the file (disk full?).');
        wp_send_json_success(['size' => filesize($dest)]);
    }

    /** Install the site zip in small steps, so a short time limit on the hosting cannot stop it. */
    public static function ajax_unzip() {
        if (!current_user_can(self::CAP) || !wp_verify_nonce(isset($_POST['_wpnonce']) ? $_POST['_wpnonce'] : '', 'vanmeya')) wp_send_json_error('Not allowed.');
        if (!class_exists('ZipArchive')) wp_send_json_error('This hosting has no zip support.');
        @set_time_limit(120);
        register_shutdown_function(function () { $e = error_get_last(); if ($e && in_array($e['type'], [E_ERROR, E_PARSE, E_CORE_ERROR, E_COMPILE_ERROR], true)) { @header('Content-Type: application/json'); echo json_encode(['success' => false, 'data' => 'The server stopped: ' . $e['message']]); } });
        $base = self::base_dir(); $new = $base . '/site_new'; $work = $base . '/work.zip'; $phase = isset($_POST['phase']) ? $_POST['phase'] : '';
        if ($phase === 'start') {
            $src = self::incoming() . '/site-for-hosting.zip';
            if (!is_file($src)) wp_send_json_error('The uploaded file is missing on the hosting. Upload it again.');
            $zip = new ZipArchive();
            if ($zip->open($src) !== true) wp_send_json_error('That file is not a valid zip (the upload may be incomplete).');
            for ($d = 0; $d < 2 && $zip->locateName('index.html') === false; $d++) {
                $inner = false;
                for ($i = 0; $i < $zip->numFiles; $i++) if (substr((string)$zip->getNameIndex($i), -4) === '.zip') { $inner = $i; break; }
                if ($inner === false) break;
                if (!@file_put_contents($base . '/inner.zip', $zip->getFromIndex($inner))) { $zip->close(); wp_send_json_error('Could not unpack the zip inside the zip (disk full?).'); }
                $zip->close(); $zip = new ZipArchive();
                if ($zip->open($base . '/inner.zip') !== true) wp_send_json_error('The zip inside the downloaded zip is not valid.');
            }
            $total = $zip->numFiles;
            if ($zip->locateName('index.html') === false) { $zip->close(); wp_send_json_error('This zip has no index.html at its top level. It has ' . $total . ' entries; first one: ' . (string)$zip->getNameIndex(0) . '. Use site-for-hosting.zip from the hosting-site run.'); }
            for ($i = 0; $i < $total; $i++) { $n = (string)$zip->getNameIndex($i); if (in_array('..', explode('/', str_replace('\\', '/', $n)), true) || $n === '' || $n[0] === '/' || strpos($n, "\0") !== false || strpos($n, ':') !== false) { $zip->close(); wp_send_json_error('The zip contains an unsafe path and was not installed.'); } }
            $zip->close();
            $from = is_file($base . '/inner.zip') ? $base . '/inner.zip' : $src;
            if (!@copy($from, $work)) wp_send_json_error('Could not copy the zip (disk full?).');
            @unlink($base . '/inner.zip');
            self::rrmdir($new); wp_mkdir_p($new);
            wp_send_json_success(['pos' => 0, 'total' => $total]);
        }
        if ($phase === 'run') {
            $pos = (int)$_POST['pos'];
            $zip = new ZipArchive();
            if (!is_file($work) || $zip->open($work) !== true) wp_send_json_error('The working copy is missing. Start again.');
            $total = $zip->numFiles; $t0 = time();
            while ($pos < $total && time() - $t0 < 12) {
                $names = [];
                for ($i = $pos; $i < min($total, $pos + 200); $i++) $names[] = $zip->getNameIndex($i);
                if (!$zip->extractTo($new, $names)) { $zip->close(); wp_send_json_error('Could not write files at ' . $pos . ' of ' . $total . ' (disk full or file limit reached?).'); }
                $pos += count($names);
            }
            $zip->close();
            wp_send_json_success(['pos' => $pos, 'total' => $total]);
        }
        if ($phase === 'finish') {
            $old = $base . '/site_old'; $cur = self::site_dir();
            if (!is_file($new . '/index.html')) wp_send_json_error('Unpacking did not produce index.html. Start again.');
            self::rrmdir($old);
            if (is_dir($cur)) rename($cur, $old);
            if (!rename($new, $cur)) wp_send_json_error('Could not switch to the new website.');
            self::rrmdir($old); @unlink($work); @unlink(self::incoming() . '/site-for-hosting.zip');
            $html = 0; foreach (new RecursiveIteratorIterator(new RecursiveDirectoryIterator($cur, FilesystemIterator::SKIP_DOTS)) as $f) if (substr($f->getFilename(), -5) === '.html') $html++;
            self::set_opt('html_pages', $html);
            wp_send_json_success(['pages' => $html, 'url' => home_url('/')]);
        }
        wp_send_json_error('Unknown step.');
    }

    /** Photos fetched by the visitor's own browser: hand out links ... */
    public static function ajax_btake() {
        if (!current_user_can(self::CAP) || !wp_verify_nonce(isset($_POST['_wpnonce']) ? $_POST['_wpnonce'] : '', 'vanmeya')) wp_send_json_error('Not allowed.');
        $st = new Vanmeya_Store_Wpdb();
        wp_send_json_success(['items' => $st->browser_take(max(2, min(14, isset($_POST['n']) ? (int)$_POST['n'] : 8))), 'counts' => self::ui_counts()]);
    }

    /** ... and receive the finished picture (or the reason it failed). */
    public static function ajax_bput() {
        if (!current_user_can(self::CAP) || !wp_verify_nonce(isset($_POST['_wpnonce']) ? $_POST['_wpnonce'] : '', 'vanmeya')) wp_send_json_error('Not allowed.');
        $st = new Vanmeya_Store_Wpdb();
        $it = $st->get((int)$_POST['id']);
        if (!$it) wp_send_json_error('Unknown photo.');
        $fail = isset($_POST['fail']) ? substr(sanitize_text_field($_POST['fail']), 0, 40) : '';
        if ($fail !== '' || empty($_FILES['img']['tmp_name']) || !is_uploaded_file($_FILES['img']['tmp_name'])) {
            $st->finish($it['id'], 'failed', true, 'browser_' . ($fail !== '' ? $fail : 'no_file'));
        } else {
            $why = self::images()->save_public($it, file_get_contents($_FILES['img']['tmp_name']));
            $why === '' ? $st->finish($it['id'], 'done', false, '') : $st->finish($it['id'], 'failed', true, 'browser_' . $why);
        }
        wp_send_json_success(['counts' => self::ui_counts()]);
    }

    public static function ui_counts() { $st = new Vanmeya_Store_Wpdb(); $c = $st->counts(); $c['skipped'] = $st->skipped(); return $c; }

    private static function run_for($seconds) {
        @set_time_limit($seconds + 30);
        $im = self::images();
        $t0 = time(); $tot = ['ok' => 0, 'failed' => 0, 'retry' => 0, 'taken' => 0, 'net' => 0];
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
            if ($n === false || in_array('..', explode('/', str_replace('\\', '/', $n)), true) || $n[0] === '/' || strpos($n, "\0") !== false || strpos($n, ':') !== false) { $zip->close(); self::back('The zip contains an unsafe path and was not installed.'); }
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
        self::rrmdir($old); @unlink(self::base_dir() . '/inner.zip');
        if (!empty($_POST['use_incoming'])) @unlink($path);
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
