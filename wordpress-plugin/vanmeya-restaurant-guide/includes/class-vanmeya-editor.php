<?php
if (!defined('ABSPATH') && !defined('VANMEYA_TEST')) { exit; }

/** Pure helpers: read the editable parts of a built page, and apply saved edits to it. No WordPress needed. */
class Vanmeya_Editor {
    public static function esc($s) { return htmlspecialchars((string)$s, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'); }

    /** The editable parts of a page, as they are now. */
    public static function extract($html) {
        $o = ['title' => '', 'desc' => '', 'h1' => '', 'intro' => '', 'article' => null];
        if (preg_match('#<title>(.*?)</title>#s', $html, $m)) $o['title'] = html_entity_decode($m[1], ENT_QUOTES, 'UTF-8');
        if (preg_match('#<meta name="description" content="([^"]*)"#', $html, $m)) $o['desc'] = html_entity_decode($m[1], ENT_QUOTES, 'UTF-8');
        if (preg_match('#<h1[^>]*>(.*?)</h1>#s', $html, $m)) $o['h1'] = trim(html_entity_decode(strip_tags($m[1]), ENT_QUOTES, 'UTF-8'));
        if (preg_match('#<p class="lede">(.*?)</p>#s', $html, $m)) $o['intro'] = trim(html_entity_decode(strip_tags($m[1]), ENT_QUOTES, 'UTF-8'));
        $a = strpos($html, '<!--vm:article-->'); $b = strpos($html, '<!--/vm:article-->');
        if ($a !== false && $b !== false && $b > $a) $o['article'] = trim(substr($html, $a + 17, $b - $a - 17));
        return $o;
    }

    private static function rep($re, $html, $fn) { return preg_replace_callback($re, $fn, $html, 1); }

    /** Put saved edits into a built page. Empty fields leave the original untouched. */
    public static function apply($html, $e) {
        if (!empty($e['title'])) {
            $t = self::esc($e['title']);
            $html = self::rep('#<title>.*?</title>#s', $html, function () use ($t) { return '<title>' . $t . '</title>'; });
            $html = self::rep('#(<meta property="og:title" content=")[^"]*(")#', $html, function ($m) use ($t) { return $m[1] . $t . $m[2]; });
        }
        if (!empty($e['desc'])) {
            $d = self::esc($e['desc']);
            $html = self::rep('#(<meta name="description" content=")[^"]*(")#', $html, function ($m) use ($d) { return $m[1] . $d . $m[2]; });
            $html = self::rep('#(<meta property="og:description" content=")[^"]*(")#', $html, function ($m) use ($d) { return $m[1] . $d . $m[2]; });
        }
        if (!empty($e['h1'])) { $h = self::esc($e['h1']); $html = self::rep('#(<h1[^>]*>).*?(</h1>)#s', $html, function ($m) use ($h) { return $m[1] . $h . $m[2]; }); }
        if (!empty($e['intro'])) { $i = self::esc($e['intro']); $html = self::rep('#(<p class="lede">).*?(</p>)#s', $html, function ($m) use ($i) { return $m[1] . $i . $m[2]; }); }
        if (isset($e['article']) && $e['article'] !== '') {
            $a = strpos($html, '<!--vm:article-->'); $b = strpos($html, '<!--/vm:article-->');
            if ($a !== false && $b !== false && $b > $a) $html = substr($html, 0, $a + 17) . "\n" . $e['article'] . "\n" . substr($html, $b);
        }
        $extra = !empty($e['extra']) ? '<div class="wrap"><section class="article vm-extra">' . $e['extra'] . '</section></div>' : '';
        return str_replace('<!--vm:extra-->', $extra, $html);
    }

    /** A brand new page, made from the blank shell the site builder writes. */
    public static function from_shell($shell, $e, $url) {
        $map = ['@@TITLE@@' => self::esc(isset($e['title']) ? $e['title'] : ''), '@@DESC@@' => self::esc(isset($e['desc']) ? $e['desc'] : ''),
                '@@URL@@' => self::esc($url), '@@H1@@' => self::esc(isset($e['h1']) && $e['h1'] !== '' ? $e['h1'] : (isset($e['title']) ? $e['title'] : '')),
                '@@BODY@@' => isset($e['article']) ? $e['article'] : ''];
        $html = strtr($shell, $map);
        return self::apply($html, ['extra' => isset($e['extra']) ? $e['extra'] : '']);
    }

    /** Clean a path typed by the user: lower case, letters, digits, dashes and slashes only. */
    public static function clean_path($p) {
        $p = strtolower(trim((string)$p));
        $p = preg_replace('#[^a-z0-9/\-]+#', '-', $p);
        $p = preg_replace('#/{2,}#', '/', $p);
        return trim($p, "/- ");
    }
}
