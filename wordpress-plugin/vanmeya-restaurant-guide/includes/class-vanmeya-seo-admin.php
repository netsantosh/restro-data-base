<?php
if (!defined('ABSPATH')) { exit; }

/** The SEO screens: audit, bulk edit in Excel, title rules, redirects, 404 log, tracking codes, robots.txt. */
class Vanmeya_Seo_Admin {
    private static $settings = null;

    public static function t_redirects() { global $wpdb; return $wpdb->prefix . 'vanmeya_redirects'; }
    public static function t_404() { global $wpdb; return $wpdb->prefix . 'vanmeya_404'; }

    public static function install() {
        global $wpdb; require_once ABSPATH . 'wp-admin/includes/upgrade.php';
        $cs = $wpdb->get_charset_collate();
        dbDelta("CREATE TABLE " . self::t_redirects() . " (
            path VARCHAR(190) NOT NULL,
            target TEXT NOT NULL,
            code SMALLINT NOT NULL DEFAULT 301,
            hits INT UNSIGNED NOT NULL DEFAULT 0,
            PRIMARY KEY  (path)
        ) $cs;");
        dbDelta("CREATE TABLE " . self::t_404() . " (
            path VARCHAR(190) NOT NULL,
            hits INT UNSIGNED NOT NULL DEFAULT 1,
            last DATETIME NOT NULL,
            PRIMARY KEY  (path)
        ) $cs;");
    }

    public static function init() {
        add_action('admin_menu', function () { add_submenu_page('vanmeya', 'SEO', 'SEO', Vanmeya_WP::CAP, 'vanmeya-seo', [__CLASS__, 'page']); }, 20);
        foreach (['rules_save', 'rules_apply', 'rules_clear', 'tracking', 'robots', 'redirect_add', 'redirect_del', 'redirect_import', 'log_clear', 'import', 'export'] as $a) add_action('admin_post_vanmeya_seo_' . $a, [__CLASS__, 'do_' . $a]);
    }

    /* ---------- settings ---------- */
    public static function settings() {
        if (self::$settings === null) { $s = get_option('vanmeya_seo', []); self::$settings = is_array($s) ? $s : []; }
        return self::$settings;
    }
    private static function set($k, $v) { $s = self::settings(); $s[$k] = $v; update_option('vanmeya_seo', $s, true); self::$settings = $s; }

    /* ---------- redirects and 404s, used while serving ---------- */
    public static function redirect_for($key) {
        global $wpdb;
        $r = $wpdb->get_row($wpdb->prepare("SELECT target, code FROM " . self::t_redirects() . " WHERE path = %s", $key), ARRAY_A);
        if ($r) $wpdb->query($wpdb->prepare("UPDATE " . self::t_redirects() . " SET hits = hits + 1 WHERE path = %s", $key));
        return $r;
    }
    public static function log_404($key) {
        global $wpdb;
        if ($key === '' || strlen($key) > 190 || preg_match('#^(wp-|favicon|apple-touch)#', $key)) return;
        $wpdb->query($wpdb->prepare("INSERT INTO " . self::t_404() . " (path, hits, last) VALUES (%s, 1, %s) ON DUPLICATE KEY UPDATE hits = hits + 1, last = VALUES(last)", $key, current_time('mysql', true)));
    }

    /* ---------- helpers ---------- */
    private static function url($args = []) { return esc_url(add_query_arg(array_merge(['page' => 'vanmeya-seo'], $args), admin_url('admin.php'))); }
    private static function guard() { if (!current_user_can(Vanmeya_WP::CAP) || !wp_verify_nonce(isset($_REQUEST['_wpnonce']) ? $_REQUEST['_wpnonce'] : '', 'vanmeya')) wp_die('Not allowed.'); }
    private static function back($tab, $msg) { wp_safe_redirect(add_query_arg(['page' => 'vanmeya-seo', 'tab' => $tab, 'vm' => rawurlencode($msg)], admin_url('admin.php'))); exit; }
    private static function in($k) { return isset($_POST[$k]) ? trim((string)wp_unslash($_POST[$k])) : ''; }
    private static function form($action, $extra = '', $multipart = false) {
        return '<form method="post"' . ($multipart ? ' enctype="multipart/form-data"' : '') . ' action="' . esc_url(admin_url('admin-post.php')) . '"><input type="hidden" name="action" value="vanmeya_seo_' . esc_attr($action) . '"><input type="hidden" name="_wpnonce" value="' . esc_attr(wp_create_nonce('vanmeya')) . '">' . $extra;
    }
    private static function rows() {
        $f = Vanmeya_WP::site_dir() . '/vm-pages.json';
        $rows = [];
        if (is_file($f)) { $j = json_decode((string)file_get_contents($f), true); if (isset($j['rows']) && is_array($j['rows'])) $rows = $j['rows']; }
        return $rows;
    }
    private static function all_edits() {
        global $wpdb; $o = [];
        foreach ($wpdb->get_results("SELECT path, data FROM " . Vanmeya_Editor_Admin::table(), ARRAY_A) as $r) { $d = json_decode($r['data'], true); if (is_array($d)) $o[$r['path']] = $d; }
        return $o;
    }
    private static function help($text) { echo '<div style="background:#f0f6fc;border-left:4px solid #72aee6;padding:10px 14px;max-width:860px;margin:10px 0">' . wp_kses_post($text) . '</div>'; }

    /* ---------- screens ---------- */
    public static function page() {
        if (!current_user_can(Vanmeya_WP::CAP)) return;
        $tab = isset($_GET['tab']) ? sanitize_key($_GET['tab']) : 'audit';
        $tabs = ['audit' => 'Check my pages', 'bulk' => 'Bulk edit in Excel', 'rules' => 'Title and description rules', 'redirects' => 'Redirects', 'log' => 'Pages not found (404)', 'tracking' => 'Google Analytics and Search Console', 'robots' => 'robots.txt and sitemap'];
        echo '<div class="wrap"><h1>SEO</h1><h2 class="nav-tab-wrapper">';
        foreach ($tabs as $k => $l) echo '<a class="nav-tab' . ($k === $tab ? ' nav-tab-active' : '') . '" href="' . self::url(['tab' => $k]) . '">' . esc_html($l) . '</a>';
        echo '</h2>';
        if (!empty($_GET['vm'])) echo '<div class="notice notice-success"><p>' . esc_html(sanitize_text_field(wp_unslash($_GET['vm']))) . '</p></div>';
        if (!is_file(Vanmeya_WP::site_dir() . '/index.html') && $tab !== 'tracking' && $tab !== 'robots') { echo '<p>Install the website first (Vanmeya, then Website).</p></div>'; return; }
        $fn = ['audit' => 'tab_audit', 'bulk' => 'tab_bulk', 'rules' => 'tab_rules', 'redirects' => 'tab_redirects', 'log' => 'tab_log', 'tracking' => 'tab_tracking', 'robots' => 'tab_robots'][$tab];
        self::$fn();
        echo '</div>';
    }

    private static function tab_audit() {
        $rows = self::rows(); $edits = self::all_edits();
        if (!$rows) { echo '<p>The list of pages is missing from the installed website. Run the hosting-site workflow again and upload the new zip.</p>'; return; }
        self::help('<strong>What this does.</strong> It checks every page for the basics Google cares about: a title, a description, a heading, and no duplicates. Click <em>Show</em> to see the pages with a problem, then <em>Edit</em> to fix them, or fix many at once in the <em>Bulk edit in Excel</em> tab.');
        $res = Vanmeya_Seo_Core::audit($rows, $edits); $labels = Vanmeya_Seo_Core::issue_labels();
        $issue = isset($_GET['issue']) ? sanitize_key($_GET['issue']) : '';
        echo '<table class="widefat striped" style="max-width:860px"><thead><tr><th>Check</th><th style="width:90px">Pages</th><th style="width:90px"></th></tr></thead><tbody>';
        foreach ($labels as $k => $l) {
            $n = isset($res[$k]) ? count($res[$k]) : 0;
            $col = $n === 0 ? '#1a7f37' : ($l[1] === 'bad' ? '#b32d2e' : ($l[1] === 'warn' ? '#996800' : '#50575e'));
            echo '<tr><td>' . esc_html($l[0]) . '</td><td style="color:' . $col . ';font-weight:600">' . number_format_i18n($n) . ($n === 0 && $k !== 'noindex' ? ' &#10004;' : '') . '</td><td>' . ($n ? '<a href="' . self::url(['tab' => 'audit', 'issue' => $k]) . '">Show</a>' : '') . '</td></tr>';
        }
        echo '</tbody></table>';
        if ($issue && isset($res[$issue])) {
            $by = []; foreach ($rows as $r) $by[$r[0]] = $r;
            echo '<h3>' . esc_html($labels[$issue][0]) . '</h3><table class="widefat striped"><thead><tr><th>Page</th><th>Now</th><th style="width:80px"></th></tr></thead><tbody>';
            foreach (array_slice($res[$issue], 0, 100) as $p) {
                $r = $by[$p]; $e = Vanmeya_Seo_Core::effective($r, isset($edits[$p]) ? $edits[$p] : null);
                $now = strpos($issue, 'desc') === 0 ? $e['desc'] : (strpos($issue, 'h1') === 0 ? $e['h1'] : $e['title']);
                echo '<tr><td><code>/' . esc_html($p) . '</code></td><td>' . esc_html($now) . ' <span class="description">(' . mb_strlen($now) . ')</span></td><td><a class="button button-small" href="' . esc_url(add_query_arg(['page' => 'vanmeya-pages', 'edit' => $p === '' ? '_home' : $p], admin_url('admin.php'))) . '">Edit</a></td></tr>';
            }
            echo '</tbody></table>';
            if (count($res[$issue]) > 100) echo '<p class="description">Showing the first 100 of ' . number_format_i18n(count($res[$issue])) . '. Use the Bulk edit tab to fix them together.</p>';
        }
    }

    private static function tab_bulk() {
        self::help('<strong>What this does.</strong> Download your pages as an Excel/CSV file, change titles, descriptions, headings, noindex or canonical in Excel, then upload the file again. Only cells you changed are saved. Leave a cell empty to keep it as it is. In the <em>noindex</em> column write <code>yes</code> to hide a page from Google or <code>no</code> to show it.');
        $rows = self::rows(); $types = []; $countries = [];
        foreach ($rows as $r) { $seg = explode('/', $r[0]); if ($seg[0] !== '' && in_array($r[1], ['place', 'area', 'restaurant'], true)) $countries[$seg[0]] = 1; }
        ksort($countries);
        echo '<h3>1. Download</h3><form method="get" action="' . esc_url(admin_url('admin-post.php')) . '"><input type="hidden" name="action" value="vanmeya_seo_export"><input type="hidden" name="_wpnonce" value="' . esc_attr(wp_create_nonce('vanmeya')) . '">';
        echo '<select name="t"><option value="">All page types</option>';
        foreach (['restaurant' => 'Restaurant pages', 'area' => 'Area pages', 'place' => 'Country, state and city pages', 'page' => 'Other pages'] as $k => $l) echo '<option value="' . esc_attr($k) . '">' . esc_html($l) . '</option>';
        echo '</select> <select name="c"><option value="">All countries</option>';
        foreach ($countries as $k => $_) echo '<option value="' . esc_attr($k) . '">' . esc_html(ucwords(str_replace('-', ' ', $k))) . '</option>';
        echo '</select> <input type="text" name="q" placeholder="Only addresses containing... (e.g. mumbai)"> <button class="button button-primary">Download CSV</button></form>';
        echo '<p class="description">Tip: for big changes download one city or one country at a time. It is faster and safer.</p>';
        echo '<h3>2. Upload the changed file</h3>' . self::form('import', '', true) . '<input type="file" name="csv" accept=".csv"> <button class="button button-primary">Upload and save changes</button></form>';
    }

    private static function tab_rules() {
        $s = self::settings(); $rows = self::rows(); $tpl = isset($s['tpl']) ? $s['tpl'] : [];
        self::help('<strong>What this does.</strong> Write one pattern for each type of page and the plugin fills in the names. Example: <code>{name}, {area}, {city} | {cuisine}</code>. Click <em>Save</em> to see a preview, <em>Apply</em> to put the patterns on all pages of that type. Pages you edited by hand are never changed. Words you can use: <code>{name} {area} {city} {state} {country} {cuisine} {site}</code>.');
        $kinds = ['restaurant' => 'Restaurant pages', 'area' => 'Area pages', 'city' => 'City pages', 'state' => 'State pages', 'country' => 'Country pages'];
        $ex = []; foreach ($rows as $r) { $k = Vanmeya_Seo_Core::kind_of($r); if (isset($kinds[$k]) && !isset($ex[$k])) $ex[$k] = $r; }
        $site = isset($s['site']) && $s['site'] !== '' ? $s['site'] : 'Vanmeya';
        echo self::form('rules_save') . '<table class="widefat striped" style="max-width:1000px"><thead><tr><th style="width:140px">Page type</th><th>Title pattern</th><th>Description pattern</th></tr></thead><tbody>';
        foreach ($kinds as $k => $l) {
            $t = isset($tpl[$k]['title']) ? $tpl[$k]['title'] : ''; $d = isset($tpl[$k]['desc']) ? $tpl[$k]['desc'] : '';
            echo '<tr><td><strong>' . esc_html($l) . '</strong></td><td><input class="large-text" name="title_' . esc_attr($k) . '" value="' . esc_attr($t) . '">';
            if (isset($ex[$k]) && $t !== '') { $p = Vanmeya_Seo_Core::render($t, $ex[$k][5], $site); echo '<div class="description">Preview: ' . esc_html($p) . ' (' . mb_strlen($p) . ')</div>'; }
            echo '</td><td><textarea class="large-text" rows="2" name="desc_' . esc_attr($k) . '">' . esc_textarea($d) . '</textarea>';
            if (isset($ex[$k]) && $d !== '') { $p = Vanmeya_Seo_Core::render($d, $ex[$k][5], $site); echo '<div class="description">Preview: ' . esc_html($p) . ' (' . mb_strlen($p) . ')</div>'; }
            echo '</td></tr>';
        }
        echo '</tbody></table><p><label>Name of your site, used for {site}: <input name="site" value="' . esc_attr($site) . '"></label></p><p><button class="button button-primary">Save patterns</button></p></form>';
        echo '<h3>Put the patterns on the pages</h3><p>' . self::form('rules_apply') . '<button class="button button-primary" onclick="return confirm(\'Apply the saved patterns to every page of each type that you have not edited by hand?\')">Apply patterns to all pages</button></form> ' . self::form('rules_clear') . '<button class="button" onclick="return confirm(\'Remove all titles and descriptions made from patterns?\')">Remove pattern results</button></form></p>';
    }

    private static function tab_redirects() {
        global $wpdb;
        self::help('<strong>What this does.</strong> Sends visitors and Google from an old address to a new one with a permanent (301) redirect, so no ranking is lost. Use it when you rename or remove a page. Addresses are written like <code>old-page</code> (no domain).');
        echo self::form('redirect_add') . '<p><input name="from" placeholder="From: old-address" class="regular-text" value="' . esc_attr(isset($_GET['from']) ? sanitize_text_field(wp_unslash($_GET['from'])) : '') . '"> &rarr; <input name="to" placeholder="To: new-address or full https:// link" class="regular-text"> <select name="code"><option value="301">301 permanent</option><option value="302">302 temporary</option></select> <button class="button button-primary">Add redirect</button></p></form>';
        echo '<p>' . self::form('redirect_import', '', true) . 'Many at once: upload a CSV with two columns, <code>from</code> and <code>to</code>. <input type="file" name="csv" accept=".csv"> <button class="button">Upload</button></form></p>';
        $list = $wpdb->get_results("SELECT path, target, code, hits FROM " . self::t_redirects() . " ORDER BY path LIMIT 300", ARRAY_A);
        echo '<table class="widefat striped"><thead><tr><th>From</th><th>To</th><th style="width:60px">Type</th><th style="width:60px">Used</th><th style="width:80px"></th></tr></thead><tbody>';
        foreach ($list as $r) echo '<tr><td><code>/' . esc_html($r['path']) . '</code></td><td>' . esc_html($r['target']) . '</td><td>' . (int)$r['code'] . '</td><td>' . (int)$r['hits'] . '</td><td>' . self::form('redirect_del', '<input type="hidden" name="from" value="' . esc_attr($r['path']) . '">') . '<button class="button button-small">Delete</button></form></td></tr>';
        if (!$list) echo '<tr><td colspan="5">No redirects yet.</td></tr>';
        echo '</tbody></table>';
    }

    private static function tab_log() {
        global $wpdb;
        self::help('<strong>What this does.</strong> Lists addresses that visitors or Google asked for but that do not exist. The ones with the most visits are worth a redirect to a working page. Click <em>Make redirect</em> to start one.');
        $list = $wpdb->get_results("SELECT path, hits, last FROM " . self::t_404() . " ORDER BY hits DESC LIMIT 100", ARRAY_A);
        echo '<table class="widefat striped"><thead><tr><th>Address</th><th style="width:70px">Visits</th><th style="width:160px">Last seen</th><th style="width:130px"></th></tr></thead><tbody>';
        foreach ($list as $r) echo '<tr><td><code>/' . esc_html($r['path']) . '</code></td><td>' . (int)$r['hits'] . '</td><td>' . esc_html(get_date_from_gmt($r['last'], 'j M Y, H:i')) . '</td><td><a class="button button-small" href="' . self::url(['tab' => 'redirects', 'from' => $r['path']]) . '">Make redirect</a></td></tr>';
        if (!$list) echo '<tr><td colspan="4">Nothing yet. That is good news.</td></tr>';
        echo '</tbody></table><p>' . self::form('log_clear') . '<button class="button">Clear this list</button></form></p>';
    }

    private static function tab_tracking() {
        $s = self::settings();
        self::help('<strong>What this does.</strong> Adds your Google Analytics, Tag Manager and Search Console verification to every page. Paste only the ID, not the whole code.');
        echo self::form('tracking') . '<table class="form-table"><tbody>';
        foreach ([['ga4', 'Google Analytics 4 ID', 'Looks like G-ABC123XYZ'], ['gtm', 'Google Tag Manager ID', 'Looks like GTM-ABC123 (use either GA4 or GTM, not both for the same tags)'], ['gsc', 'Search Console verification', 'The long code from the "HTML tag" method: only the text inside content="..."'], ['bing', 'Bing Webmaster verification', 'Optional']] as $f) {
            echo '<tr><th>' . esc_html($f[1]) . '</th><td><input class="regular-text" name="' . esc_attr($f[0]) . '" value="' . esc_attr(isset($s[$f[0]]) ? $s[$f[0]] : '') . '"><p class="description">' . esc_html($f[2]) . '</p></td></tr>';
        }
        echo '<tr><th>Other code for the head</th><td><textarea class="large-text code" rows="5" name="head">' . esc_textarea(isset($s['head']) ? $s['head'] : '') . '</textarea><p class="description">For anything else, such as a Facebook pixel. Be careful: wrong code can break every page.</p></td></tr>';
        echo '</tbody></table><p><button class="button button-primary">Save</button></p></form>';
    }

    private static function tab_robots() {
        $s = self::settings(); $def = "User-agent: *\nAllow: /\n\nSitemap: " . home_url('/sitemap.xml') . "\n";
        self::help('<strong>What this does.</strong> <code>robots.txt</code> tells search engines what they may visit. The sitemap is your list of pages for Google: it is made automatically, leaves out pages you marked noindex and includes pages you add in WordPress. Submit <code>' . esc_html(home_url('/sitemap.xml')) . '</code> in Search Console.');
        if (Vanmeya_WP::opt('noindex', 1)) echo '<div class="notice notice-warning inline"><p><strong>Your site is currently hidden from Google</strong> (staging mode), so robots.txt blocks everything. Turn this off under Vanmeya, Settings when you go live.</p></div>';
        echo self::form('robots') . '<textarea class="large-text code" rows="10" name="robots" placeholder="' . esc_attr($def) . '">' . esc_textarea(isset($s['robots']) ? $s['robots'] : '') . '</textarea><p class="description">Leave empty to use the default shown in grey.</p><p><button class="button button-primary">Save</button></p></form>';
    }

    /* ---------- actions ---------- */
    public static function do_tracking() {
        self::guard();
        foreach (['ga4', 'gtm', 'gsc', 'bing'] as $k) self::set($k, preg_replace('/[^A-Za-z0-9_\-]/', '', self::in($k)));
        self::set('head', current_user_can('unfiltered_html') ? self::in('head') : wp_kses_post(self::in('head')));
        self::back('tracking', 'Saved. The codes are on every page now.');
    }
    public static function do_robots() { self::guard(); self::set('robots', self::in('robots')); self::back('robots', 'Saved.'); }
    public static function do_rules_save() {
        self::guard(); $tpl = [];
        foreach (['restaurant', 'area', 'city', 'state', 'country'] as $k) $tpl[$k] = ['title' => sanitize_text_field(self::in('title_' . $k)), 'desc' => sanitize_text_field(self::in('desc_' . $k))];
        self::set('tpl', $tpl); self::set('site', sanitize_text_field(self::in('site')));
        self::back('rules', 'Patterns saved. Check the previews, then press Apply.');
    }
    public static function do_rules_apply() {
        self::guard(); @set_time_limit(300);
        $s = self::settings(); $tpl = isset($s['tpl']) ? $s['tpl'] : []; $site = isset($s['site']) && $s['site'] !== '' ? $s['site'] : 'Vanmeya';
        $edits = self::all_edits(); $n = 0;
        foreach (self::rows() as $r) {
            $k = Vanmeya_Seo_Core::kind_of($r); if (!isset($tpl[$k])) continue;
            $cur = isset($edits[$r[0]]) ? $edits[$r[0]] : null;
            if ($cur && empty($cur['_auto'])) continue;                  // edited by hand: leave alone
            $d = $cur ?: [];
            foreach (['title' => 2, 'desc' => 3] as $f => $ix) {
                if ($tpl[$k][$f] === '') continue;
                $v = Vanmeya_Seo_Core::render($tpl[$k][$f], $r[5], $site);
                if ($v !== '' && $v !== $r[$ix]) $d[$f] = $v; else unset($d[$f]);
            }
            $d['_auto'] = 1;
            if (count($d) > 1) { Vanmeya_Editor_Admin::put($r[0], $d, 0); $n++; } elseif ($cur) Vanmeya_Editor_Admin::remove($r[0]);
        }
        self::back('rules', 'Applied to ' . number_format_i18n($n) . ' pages.');
    }
    public static function do_rules_clear() {
        self::guard(); $n = 0;
        foreach (self::all_edits() as $p => $d) if (!empty($d['_auto'])) { Vanmeya_Editor_Admin::remove($p); $n++; }
        self::back('rules', 'Removed pattern results from ' . number_format_i18n($n) . ' pages.');
    }

    private static function norm_from($s) { $s = trim((string)$s); $s = preg_replace('#^https?://[^/]+#i', '', $s); return trim(rawurldecode($s), '/ '); }
    public static function do_redirect_add() {
        self::guard(); global $wpdb;
        $from = self::norm_from(self::in('from')); $to = self::in('to'); $code = self::in('code') === '302' ? 302 : 301;
        if ($from === '' || $to === '') self::back('redirects', 'Please fill both boxes.');
        if (!preg_match('#^https?://#i', $to)) $to = '/' . ltrim($to, '/');
        $wpdb->query($wpdb->prepare("REPLACE INTO " . self::t_redirects() . " (path, target, code, hits) VALUES (%s, %s, %d, 0)", $from, $to, $code));
        self::back('redirects', 'Redirect added.');
    }
    public static function do_redirect_del() { self::guard(); global $wpdb; $wpdb->delete(self::t_redirects(), ['path' => self::in('from')]); self::back('redirects', 'Deleted.'); }
    public static function do_redirect_import() {
        self::guard(); global $wpdb;
        if (empty($_FILES['csv']['tmp_name'])) self::back('redirects', 'Choose a CSV file first.');
        list($rows, $err) = Vanmeya_Seo_Core::read_csv($_FILES['csv']['tmp_name']);
        if ($err) self::back('redirects', $err);
        $n = 0;
        foreach ($rows as $r) {
            $from = self::norm_from(isset($r['from']) ? $r['from'] : ''); $to = trim(isset($r['to']) ? $r['to'] : '');
            if ($from === '' || $to === '') continue;
            if (!preg_match('#^https?://#i', $to)) $to = '/' . ltrim($to, '/');
            $wpdb->query($wpdb->prepare("REPLACE INTO " . self::t_redirects() . " (path, target, code, hits) VALUES (%s, %s, 301, 0)", $from, $to)); $n++;
        }
        self::back('redirects', "Added $n redirects.");
    }
    public static function do_log_clear() { self::guard(); global $wpdb; $wpdb->query("TRUNCATE TABLE " . self::t_404()); self::back('log', 'Cleared.'); }

    public static function do_export() {
        self::guard();
        $t = isset($_GET['t']) ? sanitize_key($_GET['t']) : ''; $c = isset($_GET['c']) ? sanitize_title(wp_unslash($_GET['c'])) : ''; $q = isset($_GET['q']) ? strtolower(sanitize_text_field(wp_unslash($_GET['q']))) : '';
        $edits = self::all_edits();
        nocache_headers(); header('Content-Type: text/csv; charset=utf-8'); header('Content-Disposition: attachment; filename="vanmeya-seo-' . gmdate('Ymd') . '.csv"');
        $o = fopen('php://output', 'w'); fwrite($o, "\xEF\xBB\xBF");
        fputcsv($o, ['path', 'type', 'title', 'description', 'h1', 'noindex', 'canonical']);
        foreach (self::rows() as $r) {
            $seg = explode('/', $r[0]);
            if ($t !== '' && $r[1] !== $t) continue; if ($c !== '' && $seg[0] !== $c) continue; if ($q !== '' && strpos($r[0], $q) === false) continue;
            $d = isset($edits[$r[0]]) ? $edits[$r[0]] : null; $e = Vanmeya_Seo_Core::effective($r, $d);
            fputcsv($o, [$r[0], Vanmeya_Seo_Core::kind_of($r), $e['title'], $e['desc'], $e['h1'], $e['noindex'] ? 'yes' : 'no', isset($d['canonical']) ? $d['canonical'] : '']);
        }
        fclose($o); exit;
    }
    public static function do_import() {
        self::guard(); @set_time_limit(300);
        if (empty($_FILES['csv']['tmp_name'])) self::back('bulk', 'Choose the CSV file first.');
        list($rows, $err) = Vanmeya_Seo_Core::read_csv($_FILES['csv']['tmp_name']);
        if ($err) self::back('bulk', $err);
        if (!$rows || !array_key_exists('path', $rows[0])) self::back('bulk', 'This file has no "path" column. Use a file downloaded from this page.');
        $orig = []; foreach (self::rows() as $r) $orig[$r[0]] = $r;
        $edits = self::all_edits(); $changed = 0; $skipped = 0;
        foreach ($rows as $x) {
            $p = trim($x['path'], '/ '); if (!isset($orig[$p])) { $skipped++; continue; }
            $r = $orig[$p]; $d = isset($edits[$p]) ? $edits[$p] : []; $before = $d; unset($d['_auto']);
            foreach (['title' => [2, 'title'], 'description' => [3, 'desc'], 'h1' => [4, 'h1']] as $col => $m) {
                if (!isset($x[$col])) continue; $v = trim($x[$col]); if ($v === '') continue;
                if ($v === $r[$m[0]]) unset($d[$m[1]]); else $d[$m[1]] = sanitize_text_field($v);
            }
            if (isset($x['noindex'])) { $v = strtolower(trim($x['noindex'])); if (in_array($v, ['yes', 'y', '1', 'true'], true)) $d['noindex'] = 1; elseif (in_array($v, ['no', 'n', '0', 'false'], true)) unset($d['noindex']); }
            if (isset($x['canonical'])) { $v = trim($x['canonical']); if ($v === '') { /* keep */ } elseif (preg_match('#^https?://#i', $v)) $d['canonical'] = esc_url_raw($v); }
            $b = $before; if ($d == $b) continue;
            if ($d) Vanmeya_Editor_Admin::put($p, $d, 0); else Vanmeya_Editor_Admin::remove($p);
            $changed++;
        }
        self::back('bulk', "Saved changes on $changed pages." . ($skipped ? " $skipped rows were skipped because their address was not found." : ''));
    }
}
