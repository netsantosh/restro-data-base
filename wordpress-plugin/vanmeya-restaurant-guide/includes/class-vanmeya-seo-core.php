<?php
if (!defined('ABSPATH') && !defined('VANMEYA_TEST')) { exit; }

/** SEO helpers that need no WordPress, so they can be tested on their own. */
class Vanmeya_Seo_Core {
    const TITLE_MAX = 60; const TITLE_MIN = 20; const DESC_MAX = 160; const DESC_MIN = 70;

    /** What kind of page a manifest row is, in words an SEO person uses. */
    public static function kind_of($row) {
        $k = $row[1];
        if ($k === 'place') { $v = isset($row[5]) && is_array($row[5]) ? $row[5] : []; return isset($v['city']) ? 'city' : (isset($v['state']) ? 'state' : 'country'); }
        return $k;
    }

    /** Fill {name} {city} {area} {state} {country} {cuisine} {site} in a pattern and tidy the result. */
    public static function render($tpl, $vars, $site = '') {
        $vars = is_array($vars) ? $vars : [];
        $vars['site'] = $site;
        $out = preg_replace_callback('/\{(name|city|area|state|country|cuisine|site)\}/', function ($m) use ($vars) { return isset($vars[$m[1]]) ? $vars[$m[1]] : ''; }, (string)$tpl);
        $out = preg_replace('/\s*,\s*(,\s*)+/', ', ', $out);
        $out = preg_replace('/\s*\|\s*(\|\s*)+/', ' | ', $out);
        $out = preg_replace('/\s+/', ' ', $out);
        return trim($out, " ,|-:");
    }

    /** Effective title, description and heading of a row after the saved edits. */
    public static function effective($row, $edit) {
        $e = is_array($edit) ? $edit : [];
        return [
            'title' => isset($e['title']) && $e['title'] !== '' ? $e['title'] : $row[2],
            'desc' => isset($e['desc']) && $e['desc'] !== '' ? $e['desc'] : (isset($row[3]) ? $row[3] : ''),
            'h1' => isset($e['h1']) && $e['h1'] !== '' ? $e['h1'] : (isset($row[4]) ? $row[4] : ''),
            'noindex' => !empty($e['noindex']),
        ];
    }

    /** Find problems. Returns [issue => [paths]] with a readable label list in self::issue_labels(). */
    public static function audit($rows, $edits) {
        $res = []; $titles = []; $descs = [];
        foreach ($rows as $r) {
            if ($r[1] === 'custom') continue;
            $e = self::effective($r, isset($edits[$r[0]]) ? $edits[$r[0]] : null);
            $p = $r[0];
            if ($e['noindex']) $res['noindex'][] = $p;
            $tl = mb_strlen($e['title']); $dl = mb_strlen($e['desc']);
            if ($tl === 0) $res['title_missing'][] = $p; elseif ($tl > self::TITLE_MAX) $res['title_long'][] = $p; elseif ($tl < self::TITLE_MIN) $res['title_short'][] = $p;
            if ($dl === 0) $res['desc_missing'][] = $p; elseif ($dl > self::DESC_MAX) $res['desc_long'][] = $p; elseif ($dl < self::DESC_MIN) $res['desc_short'][] = $p;
            if ($e['h1'] === '' && !in_array($r[1], ['home', 'page'], true)) $res['h1_missing'][] = $p;
            if (!$e['noindex']) { if ($e['title'] !== '') $titles[mb_strtolower($e['title'])][] = $p; if ($e['desc'] !== '') $descs[mb_strtolower($e['desc'])][] = $p; }
        }
        foreach ($titles as $t => $ps) if (count($ps) > 1) foreach ($ps as $p) $res['title_dup'][] = $p;
        foreach ($descs as $t => $ps) if (count($ps) > 1) foreach ($ps as $p) $res['desc_dup'][] = $p;
        return $res;
    }

    public static function issue_labels() {
        return [
            'title_missing' => ['No Google title', 'bad'], 'title_long' => ['Title longer than 60 characters (Google may cut it)', 'warn'], 'title_short' => ['Title shorter than 20 characters', 'warn'],
            'title_dup' => ['Same title used on more than one page', 'bad'],
            'desc_missing' => ['No Google description', 'bad'], 'desc_long' => ['Description longer than 160 characters (Google may cut it)', 'warn'], 'desc_short' => ['Description shorter than 70 characters', 'warn'],
            'desc_dup' => ['Same description used on more than one page', 'warn'],
            'h1_missing' => ['No page heading (H1)', 'bad'], 'noindex' => ['Hidden from Google on purpose (noindex)', 'info'],
        ];
    }

    /** Extra tags for the <head>: verification, Google Analytics, your own code, page-level structured data. */
    public static function head_code($seo, $edit) {
        $o = '';
        if (!empty($seo['gsc'])) $o .= '<meta name="google-site-verification" content="' . Vanmeya_Editor::esc($seo['gsc']) . '">' . "\n";
        if (!empty($seo['bing'])) $o .= '<meta name="msvalidate.01" content="' . Vanmeya_Editor::esc($seo['bing']) . '">' . "\n";
        if (!empty($seo['ga4']) && preg_match('/^(G|GT|AW)-[A-Z0-9]{4,}$/i', $seo['ga4'])) {
            $id = $seo['ga4'];
            $o .= '<script async src="https://www.googletagmanager.com/gtag/js?id=' . $id . '"></script><script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments)}gtag("js",new Date());gtag("config","' . $id . '");</script>' . "\n";
        }
        if (!empty($seo['gtm']) && preg_match('/^GTM-[A-Z0-9]{4,}$/i', $seo['gtm'])) {
            $o .= '<script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({"gtm.start":new Date().getTime(),event:"gtm.js"});var f=d.getElementsByTagName(s)[0],j=d.createElement(s);j.async=true;j.src="https://www.googletagmanager.com/gtm.js?id="+i;f.parentNode.insertBefore(j,f);})(window,document,"script","dataLayer","' . $seo['gtm'] . '");</script>' . "\n";
        }
        if (!empty($seo['head'])) $o .= $seo['head'] . "\n";
        if (!empty($edit['schema'])) { $j = json_decode($edit['schema'], true); if (is_array($j)) $o .= '<script type="application/ld+json">' . json_encode($j, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) . '</script>' . "\n"; }
        return $o;
    }

    /** Apply the site-wide SEO settings and the page's own robots, canonical and image settings. */
    public static function decorate($html, $seo, $edit) {
        $head = self::head_code($seo, $edit);
        $html = str_replace('<!--vm:head-->', $head, $html);
        if (!empty($edit['noindex'])) $html = preg_replace_callback('#<meta name="robots" content="[^"]*">#', function () { return '<meta name="robots" content="noindex,follow">'; }, $html, 1);
        if (!empty($edit['canonical']) && preg_match('#^https?://#i', $edit['canonical'])) {
            $c = Vanmeya_Editor::esc($edit['canonical']);
            $html = preg_replace_callback('#(<link rel="canonical" href=")[^"]*(")#', function ($m) use ($c) { return $m[1] . $c . $m[2]; }, $html, 1);
            $html = preg_replace_callback('#(<meta property="og:url" content=")[^"]*(")#', function ($m) use ($c) { return $m[1] . $c . $m[2]; }, $html, 1);
        }
        if (!empty($edit['ogimage']) && preg_match('#^https?://#i', $edit['ogimage'])) {
            $c = Vanmeya_Editor::esc($edit['ogimage']);
            $html = preg_replace_callback('#(<meta property="og:image" content=")[^"]*(")#', function ($m) use ($c) { return $m[1] . $c . $m[2]; }, $html, 1);
        }
        return $html;
    }

    /** Take pages marked noindex out of the sitemap and add pages created in WordPress. */
    public static function filter_sitemap($xml, $drop_paths, $add_urls) {
        if ($drop_paths) {
            $drop = array_flip(array_map(function ($p) { return trim($p, '/'); }, $drop_paths));
            $xml = preg_replace_callback('#[ \t]*<url><loc>([^<]*)</loc>.*?</url>\n?#s', function ($m) use ($drop) {
                $path = trim((string)parse_url(html_entity_decode($m[1]), PHP_URL_PATH), '/');
                return isset($drop[$path]) ? '' : $m[0];
            }, $xml);
        }
        if ($add_urls) {
            $add = '';
            foreach ($add_urls as $u) $add .= '  <url><loc>' . htmlspecialchars($u, ENT_XML1) . '</loc></url>' . "\n";
            $xml = str_replace('</urlset>', $add . '</urlset>', $xml);
        }
        return $xml;
    }

    /** Parse a CSV file into rows of header=>value, tolerant of Excel's byte order mark. */
    public static function read_csv($path) {
        $fh = @fopen($path, 'r'); if (!$fh) return [[], 'Could not read the file.'];
        $head = fgetcsv($fh); if (!$head) { fclose($fh); return [[], 'The file is empty.']; }
        $head[0] = preg_replace('/^\xEF\xBB\xBF/', '', (string)$head[0]);
        $head = array_map(function ($h) { return strtolower(trim((string)$h)); }, $head);
        $rows = [];
        while (($r = fgetcsv($fh)) !== false) { if (count($r) === 1 && $r[0] === null) continue; $row = []; foreach ($head as $i => $h) $row[$h] = isset($r[$i]) ? $r[$i] : ''; $rows[] = $row; }
        fclose($fh);
        return [$rows, ''];
    }
}
