<?php
if (!defined('ABSPATH')) { exit; }

/** The "Pages & content" screens in the WordPress admin. Edits are kept in their own table and applied when a page is served. */
class Vanmeya_Editor_Admin {
    const PER = 40;
    private static $rows = null;

    public static function table() { global $wpdb; return $wpdb->prefix . 'vanmeya_edits'; }

    public static function install() {
        global $wpdb;
        require_once ABSPATH . 'wp-admin/includes/upgrade.php';
        dbDelta("CREATE TABLE " . self::table() . " (
            path VARCHAR(190) NOT NULL,
            data LONGTEXT NOT NULL,
            custom TINYINT(1) NOT NULL DEFAULT 0,
            updated DATETIME NOT NULL,
            PRIMARY KEY  (path)
        ) " . $wpdb->get_charset_collate() . ";");
    }

    public static function init() {
        add_action('admin_menu', function () { add_submenu_page('vanmeya', 'Pages & content', 'Pages & content', Vanmeya_WP::CAP, 'vanmeya-pages', [__CLASS__, 'page']); });
        add_action('admin_post_vanmeya_edit_save', [__CLASS__, 'save']);
        add_action('admin_post_vanmeya_edit_reset', [__CLASS__, 'reset']);
        add_action('admin_post_vanmeya_new', [__CLASS__, 'create']);
    }

    /* ---------- storage ---------- */
    public static function get($key) {
        global $wpdb;
        $r = $wpdb->get_row($wpdb->prepare("SELECT data, custom FROM " . self::table() . " WHERE path = %s", $key), ARRAY_A);
        if (!$r) return null;
        $d = json_decode($r['data'], true);
        return is_array($d) ? ['data' => $d, 'custom' => (int)$r['custom']] : null;
    }
    private static function put($key, $data, $custom) {
        global $wpdb;
        $wpdb->query($wpdb->prepare("REPLACE INTO " . self::table() . " (path, data, custom, updated) VALUES (%s, %s, %d, %s)", $key, wp_json_encode($data), $custom, current_time('mysql', true)));
    }
    private static function edited_keys() {
        global $wpdb; $o = [];
        foreach ($wpdb->get_results("SELECT path, custom FROM " . self::table(), ARRAY_A) as $r) $o[$r['path']] = (int)$r['custom'] ? 'custom' : 'edited';
        return $o;
    }

    /* ---------- the list of pages the site builder wrote ---------- */
    private static function rows() {
        if (self::$rows !== null) return self::$rows;
        $f = Vanmeya_WP::site_dir() . '/vm-pages.json';
        $rows = [];
        if (is_file($f)) { $j = json_decode((string)file_get_contents($f), true); if (isset($j['rows']) && is_array($j['rows'])) $rows = $j['rows']; }
        foreach (self::edited_keys() as $k => $kind) if ($kind === 'custom') $rows[] = [$k, 'custom', ''];
        return self::$rows = $rows;
    }
    private static function file_for($key) { return Vanmeya_WP::site_dir() . '/' . ($key === '' ? '' : $key . '/') . 'index.html'; }

    /* ---------- screens ---------- */
    private static function link($args) { return esc_url(add_query_arg(array_merge(['page' => 'vanmeya-pages'], $args), admin_url('admin.php'))); }

    public static function page() {
        if (!current_user_can(Vanmeya_WP::CAP)) return;
        echo '<div class="wrap"><h1>Pages &amp; content <a class="page-title-action" href="' . self::link(['new' => 1]) . '">Add new page</a></h1>';
        if (!empty($_GET['vm'])) echo '<div class="notice notice-success"><p>' . esc_html(sanitize_text_field(wp_unslash($_GET['vm']))) . '</p></div>';
        if (!is_file(Vanmeya_WP::site_dir() . '/index.html')) { echo '<p>The website is not installed yet. Install it first on the Vanmeya page (step 3).</p></div>'; return; }
        if (isset($_GET['new'])) { self::form_new(); echo '</div>'; return; }
        if (isset($_GET['edit'])) { self::form_edit(sanitize_text_field(wp_unslash($_GET['edit']))); echo '</div>'; return; }
        self::listing();
        echo '</div>';
    }

    private static function listing() {
        $rows = self::rows();
        if (!$rows || !is_file(Vanmeya_WP::site_dir() . '/vm-pages.json')) echo '<div class="notice notice-warning"><p>The list of pages is missing from the installed website. Build the website again with the latest code and install the new zip, then this list appears.</p></div>';
        $type = isset($_GET['t']) ? sanitize_key($_GET['t']) : '';
        $c = isset($_GET['c']) ? sanitize_title(wp_unslash($_GET['c'])) : '';
        $s = isset($_GET['s']) ? sanitize_title(wp_unslash($_GET['s'])) : '';
        $q = isset($_GET['q']) ? strtolower(sanitize_text_field(wp_unslash($_GET['q']))) : '';
        $only = !empty($_GET['only']);
        $countries = []; $states = [];
        foreach ($rows as $r) { $seg = explode('/', $r[0]); if (in_array($r[1], ['place', 'area', 'restaurant'], true) && $seg[0] !== '') { $countries[$seg[0]] = 1; if ($c !== '' && $seg[0] === $c && isset($seg[1])) $states[$seg[1]] = 1; } }
        ksort($countries); ksort($states);
        $edited = self::edited_keys();
        echo '<form method="get" style="margin:12px 0"><input type="hidden" name="page" value="vanmeya-pages">';
        echo '<select name="t"><option value="">All page types</option>';
        foreach (['home' => 'Home', 'place' => 'Country, state and city pages', 'area' => 'Area pages', 'restaurant' => 'Restaurant pages', 'page' => 'Other pages (About, Contact...)', 'custom' => 'Pages I added'] as $k => $l) echo '<option value="' . esc_attr($k) . '"' . selected($type, $k, false) . '>' . esc_html($l) . '</option>';
        echo '</select> <select name="c"><option value="">All countries</option>';
        foreach ($countries as $k => $_) echo '<option value="' . esc_attr($k) . '"' . selected($c, $k, false) . '>' . esc_html(ucwords(str_replace('-', ' ', $k))) . '</option>';
        echo '</select> ';
        if ($c !== '') { echo '<select name="s"><option value="">All states</option>'; foreach ($states as $k => $_) echo '<option value="' . esc_attr($k) . '"' . selected($s, $k, false) . '>' . esc_html(ucwords(str_replace('-', ' ', $k))) . '</option>'; echo '</select> '; }
        echo '<input type="search" name="q" value="' . esc_attr($q) . '" placeholder="Search name or address"> <label><input type="checkbox" name="only" value="1"' . checked($only, true, false) . '> only pages I changed</label> <button class="button">Filter</button> <a class="button-link" href="' . self::link([]) . '">Clear</a></form>';
        $out = [];
        foreach ($rows as $r) {
            $seg = explode('/', $r[0]);
            if ($type !== '' && $r[1] !== $type) continue;
            if ($c !== '' && $seg[0] !== $c) continue;
            if ($s !== '' && (!isset($seg[1]) || $seg[1] !== $s)) continue;
            if ($q !== '' && strpos(strtolower($r[0] . ' ' . $r[2]), $q) === false) continue;
            if ($only && !isset($edited[$r[0]])) continue;
            $out[] = $r;
        }
        $n = count($out); $pg = max(1, isset($_GET['paged']) ? (int)$_GET['paged'] : 1);
        echo '<p>' . number_format_i18n($n) . ' pages match.</p><table class="widefat striped"><thead><tr><th>Page</th><th style="width:120px">Type</th><th>Address</th><th style="width:90px">Status</th><th style="width:130px"></th></tr></thead><tbody>';
        foreach (array_slice($out, ($pg - 1) * self::PER, self::PER) as $r) {
            $key = $r[0]; $arg = $key === '' ? '_home' : $key;
            $label = $r[2] !== '' ? $r[2] : ($key === '' ? 'Home' : $key);
            echo '<tr><td><strong>' . esc_html($label) . '</strong></td><td>' . esc_html($r[1]) . '</td><td><code>/' . esc_html($key) . '</code></td><td>' . (isset($edited[$key]) ? '<span style="color:#1a7f37">' . ($edited[$key] === 'custom' ? 'Added' : 'Changed') . '</span>' : '') . '</td>';
            echo '<td><a class="button button-small" href="' . self::link(['edit' => $arg]) . '">Edit</a> <a class="button button-small" target="_blank" href="' . esc_url(home_url('/' . $key . ($key === '' ? '' : '/'))) . '">View</a></td></tr>';
        }
        if (!$out) echo '<tr><td colspan="5">No pages match.</td></tr>';
        echo '</tbody></table>';
        $pages = (int)ceil($n / self::PER);
        if ($pages > 1) echo '<div class="tablenav"><div class="tablenav-pages">' . paginate_links(['base' => add_query_arg('paged', '%#%'), 'format' => '', 'current' => $pg, 'total' => $pages]) . '</div></div>';
    }

    private static function field($label, $name, $val, $help = '', $area = false) {
        echo '<tr><th style="width:170px"><label>' . esc_html($label) . '</label></th><td>';
        if ($area) echo '<textarea name="' . esc_attr($name) . '" rows="3" class="large-text">' . esc_textarea($val) . '</textarea>';
        else echo '<input type="text" name="' . esc_attr($name) . '" value="' . esc_attr($val) . '" class="large-text">';
        if ($help) echo '<p class="description">' . esc_html($help) . '</p>';
        echo '</td></tr>';
    }

    private static function form_edit($arg) {
        $key = $arg === '_home' ? '' : Vanmeya_Editor::clean_path($arg);
        $saved = self::get($key); $custom = $saved && $saved['custom'];
        $file = self::file_for($key);
        $orig = ['title' => '', 'desc' => '', 'h1' => '', 'intro' => '', 'article' => null];
        if (is_file($file)) $orig = Vanmeya_Editor::extract((string)file_get_contents($file));
        elseif (!$custom) { echo '<p>That page does not exist in the installed website.</p>'; return; }
        $d = $saved ? $saved['data'] : [];
        $val = function ($k) use ($d, $orig) { return isset($d[$k]) && $d[$k] !== '' ? $d[$k] : (isset($orig[$k]) ? $orig[$k] : ''); };
        echo '<p><a href="' . self::link([]) . '">&larr; All pages</a> &nbsp; <a target="_blank" href="' . esc_url(home_url('/' . $key . ($key === '' ? '' : '/'))) . '">View this page</a></p><h2>' . esc_html($key === '' ? 'Home' : '/' . $key) . '</h2>';
        echo '<form method="post" action="' . esc_url(admin_url('admin-post.php')) . '"><input type="hidden" name="action" value="vanmeya_edit_save"><input type="hidden" name="path" value="' . esc_attr($arg) . '">' . wp_nonce_field('vanmeya', '_wpnonce', true, false);
        echo '<table class="form-table"><tbody>';
        self::field('Google title', 'title', $val('title'), 'The title in browser tabs and Google results. About 60 characters works well.');
        self::field('Google description', 'desc', $val('desc'), 'The short text under the title in Google results. About 150 characters.', true);
        self::field('Page heading (H1)', 'h1', $val('h1'));
        if (!$custom && $orig['intro'] !== '') self::field('Intro line', 'intro', $val('intro'), 'The sentence under the heading.', true);
        echo '</tbody></table>';
        if ($custom || $orig['article'] !== null) {
            echo '<h3>' . ($custom ? 'Page content' : 'Main text of this page') . '</h3>';
            if (!$custom) echo '<p class="description">This replaces the text block of the page (paragraphs, FAQ). Leave it as it is to keep the original.</p>';
            wp_editor(isset($d['article']) && $d['article'] !== '' ? $d['article'] : (string)$orig['article'], 'vm_article', ['textarea_name' => 'article', 'textarea_rows' => 14, 'media_buttons' => false]);
        }
        echo '<h3>Extra content to add</h3><p class="description">Anything you write here is added at the bottom of the page. Works on every page.</p>';
        wp_editor(isset($d['extra']) ? $d['extra'] : '', 'vm_extra', ['textarea_name' => 'extra', 'textarea_rows' => 8, 'media_buttons' => false]);
        echo '<p><button class="button button-primary">Save changes</button> ';
        echo '<a class="button" href="' . self::link([]) . '">Cancel</a></p></form>';
        if ($saved) echo '<form method="post" action="' . esc_url(admin_url('admin-post.php')) . '" onsubmit="return confirm(\'Remove all your changes from this page?\')"><input type="hidden" name="action" value="vanmeya_edit_reset"><input type="hidden" name="path" value="' . esc_attr($arg) . '">' . wp_nonce_field('vanmeya', '_wpnonce', true, false) . '<button class="button">' . ($custom ? 'Delete this page' : 'Go back to the original page') . '</button></form>';
        echo '<p class="description">Your changes are kept in WordPress, so they stay when you upload a new website zip. Changes made in the Google Sheets only show where you have not made a change here.</p>';
    }

    private static function form_new() {
        echo '<p><a href="' . self::link([]) . '">&larr; All pages</a></p><h2>Add a new page</h2><form method="post" action="' . esc_url(admin_url('admin-post.php')) . '"><input type="hidden" name="action" value="vanmeya_new">' . wp_nonce_field('vanmeya', '_wpnonce', true, false) . '<table class="form-table"><tbody>';
        self::field('Page address', 'newpath', '', 'For example guides/best-biryani-in-hyderabad . Letters, numbers and dashes only. It becomes ' . home_url('/') . 'guides/best-biryani-in-hyderabad/');
        self::field('Google title', 'title', '');
        self::field('Google description', 'desc', '', '', true);
        self::field('Page heading (H1)', 'h1', '');
        echo '</tbody></table>';
        wp_editor('', 'vm_article', ['textarea_name' => 'article', 'textarea_rows' => 14, 'media_buttons' => false]);
        echo '<p><button class="button button-primary">Create page</button></p></form>';
    }

    /* ---------- actions ---------- */
    private static function guard() { if (!current_user_can(Vanmeya_WP::CAP) || !wp_verify_nonce(isset($_POST['_wpnonce']) ? $_POST['_wpnonce'] : '', 'vanmeya')) wp_die('Not allowed.'); }
    private static function back($args, $msg) { wp_safe_redirect(add_query_arg(array_merge(['page' => 'vanmeya-pages', 'vm' => rawurlencode($msg)], $args), admin_url('admin.php'))); exit; }
    private static function in($k) { return isset($_POST[$k]) ? trim(wp_unslash($_POST[$k])) : ''; }

    public static function save() {
        self::guard();
        $arg = self::in('path'); $key = $arg === '_home' ? '' : Vanmeya_Editor::clean_path($arg);
        $saved = self::get($key); $custom = $saved && $saved['custom'];
        $orig = ['title' => '', 'desc' => '', 'h1' => '', 'intro' => '', 'article' => null];
        if (is_file(self::file_for($key))) $orig = Vanmeya_Editor::extract((string)file_get_contents(self::file_for($key)));
        $d = [];
        foreach (['title', 'desc', 'h1', 'intro'] as $k) {
            $v = sanitize_text_field(self::in($k));
            if ($v !== '' && ($custom || $v !== $orig[$k])) $d[$k] = $v;
        }
        $art = isset($_POST['article']) ? trim(wp_kses_post(wp_unslash($_POST['article']))) : '';
        $norm = function ($s) { return preg_replace('/\s+/', ' ', trim((string)$s)); };
        if ($art !== '' && ($custom || $orig['article'] === null || $norm($art) !== $norm($orig['article']))) $d['article'] = $art;
        $extra = isset($_POST['extra']) ? trim(wp_kses_post(wp_unslash($_POST['extra']))) : '';
        if ($extra !== '') $d['extra'] = $extra;
        if (!$d && !$custom) { global $wpdb; $wpdb->delete(self::table(), ['path' => $key]); self::back(['edit' => $arg], 'Nothing was changed from the original.'); }
        self::put($key, $d, $custom ? 1 : 0);
        self::back(['edit' => $arg], 'Saved. The page is updated now.');
    }

    public static function reset() {
        self::guard();
        $arg = self::in('path'); $key = $arg === '_home' ? '' : Vanmeya_Editor::clean_path($arg);
        global $wpdb; $wpdb->delete(self::table(), ['path' => $key]);
        self::back([], 'Done.');
    }

    public static function create() {
        self::guard();
        $key = Vanmeya_Editor::clean_path(self::in('newpath'));
        if ($key === '' || strlen($key) > 180) self::back(['new' => 1], 'Please type a page address.');
        if (preg_match('#^(wp-|static|search-index|sitemap|robots|vm-|_vm)#', $key)) self::back(['new' => 1], 'That address is reserved. Choose another.');
        if (is_file(self::file_for($key))) self::back(['new' => 1], 'A page already exists at that address. Edit it from the list instead.');
        $title = sanitize_text_field(self::in('title'));
        if ($title === '') self::back(['new' => 1], 'Please type a title.');
        $d = ['title' => $title, 'desc' => sanitize_text_field(self::in('desc')), 'h1' => sanitize_text_field(self::in('h1')), 'article' => trim(wp_kses_post(wp_unslash(isset($_POST['article']) ? $_POST['article'] : '')))];
        self::put($key, $d, 1);
        self::back(['edit' => $key], 'Page created: ' . home_url('/' . $key . '/'));
    }
}
