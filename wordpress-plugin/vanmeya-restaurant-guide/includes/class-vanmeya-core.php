<?php
/**
 * Vanmeya core: no WordPress functions in this file, so it can be tested from the command line.
 *
 *  - Vanmeya_Csv        reads the images database CSV
 *  - Vanmeya_Images     downloads photos (several at a time) into one folder per restaurant
 *  - Vanmeya_Store_*    the download queue (WordPress database table, or memory for tests)
 */
if (!defined('VANMEYA_CORE')) { define('VANMEYA_CORE', 1); }

/** Same rule as photo_folder() in build.py: the folder name is the restaurant's place_id with unsafe characters removed. */
function vanmeya_folder($place_id) {
    return preg_replace('/[^A-Za-z0-9_-]/', '', (string)$place_id);
}

class Vanmeya_Csv {
    /** Returns [rows, error]. Each row: place_id, name, urls[] (photo_1..photo_N in order), checked_at. */
    public static function read($path, $max_photos = 10) {
        $f = @fopen($path, 'r');
        if (!$f) return [[], 'Could not open the file.'];
        $bom = fread($f, 3);
        if ($bom !== "\xEF\xBB\xBF") rewind($f);
        $head = fgetcsv($f, 0, ',', '"', '\\');
        if (!$head) return [[], 'The file is empty.'];
        $head = array_map(function ($h) { return strtolower(trim((string)$h)); }, $head);
        $idx = array_flip($head);
        if (!isset($idx['place_id']) || !isset($idx['photo_1'])) {
            fclose($f);
            return [[], 'This is not an images database: the columns place_id and photo_1 were not found.'];
        }
        $rows = [];
        while (($r = fgetcsv($f, 0, ',', '"', '\\')) !== false) {
            if (!isset($r[$idx['place_id']])) continue;
            $pid = trim($r[$idx['place_id']]);
            if ($pid === '') continue;
            $urls = [];
            for ($i = 1; $i <= $max_photos; $i++) {
                $c = 'photo_' . $i;
                if (isset($idx[$c]) && isset($r[$idx[$c]])) {
                    $u = trim($r[$idx[$c]]);
                    if (preg_match('#^https?://#i', $u)) $urls[] = $u;
                }
            }
            $rows[] = [
                'place_id' => $pid,
                'name' => isset($idx['name'], $r[$idx['name']]) ? $r[$idx['name']] : '',
                'urls' => $urls,
                'checked_at' => isset($idx['checked_at'], $r[$idx['checked_at']]) ? $r[$idx['checked_at']] : '',
            ];
        }
        fclose($f);
        return [$rows, ''];
    }
}

/** Queue in memory (tests). The WordPress version has the same methods on a database table. */
class Vanmeya_Store_Memory {
    public $items = [];   // id => [id, place_id, idx, url, status, tries]
    private $next = 1;
    public function reset() { $this->items = []; $this->next = 1; }
    /** Adds a photo to the queue. A photo that is already stored with the same link is left alone. */
    public function add($place_id, $idx, $url) {
        foreach ($this->items as &$it) {
            if ($it['place_id'] === $place_id && $it['idx'] === $idx) {
                if ($it['url'] === $url && $it['status'] === 'done') return;
                $it['url'] = $url; $it['status'] = 'pending'; $it['tries'] = 0;
                return;
            }
        }
        $this->items[$this->next] = ['id' => $this->next, 'place_id' => $place_id, 'idx' => $idx, 'url' => $url, 'status' => 'pending', 'tries' => 0];
        $this->next++;
    }
    public function take($n, $max_tries) {
        $out = [];
        foreach ($this->items as $it) {
            if ($it['status'] === 'pending' && $it['tries'] < $max_tries) { $out[] = $it; if (count($out) >= $n) break; }
        }
        return $out;
    }
    public function finish($id, $status, $bump_tries, $note = '') {
        $this->items[$id]['status'] = $status;
        $this->items[$id]['note'] = $note;
        if ($bump_tries) $this->items[$id]['tries']++;
    }
    public function counts() {
        $c = ['pending' => 0, 'done' => 0, 'failed' => 0];
        foreach ($this->items as $it) { $c[$it['status']] = ($c[$it['status']] ?? 0) + 1; }
        return $c;
    }
    public function reasons() {
        $r = [];
        foreach ($this->items as $it) { if ($it['status'] === 'failed') { $k = $it['note'] ?? '?'; $r[$k] = ($r[$k] ?? 0) + 1; } }
        arsort($r);
        return $r;
    }
}

class Vanmeya_Images {
    public $dir;            // photo root, one folder per restaurant inside
    public $store;
    public $max_tries = 3;       // for answers from Google such as 5xx
    public $net_tries = 8;       // for 'could not connect' problems, which are usually temporary on shared hosting
    public $parallel = 4;
    public $max_width = 800;
    public $quality = 78;
    public $min_side = 200;     // smaller images are logos or icons
    public $timeout = 25;

    public function __construct($dir, $store) { $this->dir = rtrim($dir, '/'); $this->store = $store; }

    public static function can_make_webp() { return function_exists('imagewebp') && function_exists('imagecreatefromstring'); }

    /** Put a CSV's rows into the queue. Returns the number of photos queued. */
    public function enqueue($rows) {
        $n = 0;
        foreach ($rows as $r) {
            $folder = vanmeya_folder($r['place_id']);
            if ($folder === '') continue;
            foreach ($r['urls'] as $i => $u) {
                $idx = $i + 1;
                if (is_file($this->dir . "/$folder/$idx.webp") && $this->stored_url($folder, $idx) === $u) continue;
                $this->store->add($folder, $idx, $u);
                $n++;
            }
        }
        return $n;
    }

    private function stored_url($folder, $idx) {
        $p = $this->dir . "/$folder/.src";
        if (!is_file($p)) return '';
        $map = json_decode((string)@file_get_contents($p), true);
        return is_array($map) && isset($map[$idx]) ? $map[$idx] : '';
    }

    private function remember($folder, $idx, $url) {
        $p = $this->dir . "/$folder/.src";
        $map = is_file($p) ? json_decode((string)@file_get_contents($p), true) : [];
        if (!is_array($map)) $map = [];
        $map[$idx] = $url;
        @file_put_contents($p, json_encode($map), LOCK_EX);
    }

    /** Download up to $batch queued photos (in parallel) and store them. Returns counts for this step. */
    public function step($batch = 24) {
        $items = $this->store->take($batch, $this->net_tries);
        $res = ['ok' => 0, 'failed' => 0, 'retry' => 0, 'taken' => count($items), 'net' => 0];
        if (!$items) return $res;
        foreach (array_chunk($items, $this->parallel) as $group) {
            $got = $this->fetch_many(array_column($group, 'url'));
            foreach ($group as $k => $it) {
                list($body, $code, $err) = $got[$k];
                $net = false;
                if ($body === null) {
                    if ($code === 0) { $net = true; $why = 'net: ' . substr($err !== '' ? $err : 'no answer', 0, 34); $res['net']++; }
                    else $why = 'http_' . $code;
                    $perm = in_array($code, [400, 401, 403, 404, 410], true);
                } else { $why = $this->save($it, $body); $perm = in_array($why, ['too_small', 'not_an_image'], true); }
                $limit = $net ? $this->net_tries : $this->max_tries;
                if ($why === '') { $this->store->finish($it['id'], 'done', false, ''); $res['ok']++; }
                elseif ($perm || $it['tries'] + 1 >= $limit) { $this->store->finish($it['id'], 'failed', true, $why); $res['failed']++; }
                else { $this->store->finish($it['id'], 'pending', true, $why); $res['retry']++; }
            }
            if ($res['net'] > 0 && $res['ok'] === 0) { sleep(3); break; }   // the connection is struggling: slow down instead of burning the retries
        }
        return $res;
    }

    /** Several downloads at once with curl_multi. Returns [body or null, http code (0 = no connection), error text] for each url, in the same order. */
    public function fetch_many($urls) {
        $mh = curl_multi_init();
        $handles = [];
        foreach ($urls as $k => $u) {
            $ch = curl_init($u);
            curl_setopt_array($ch, [
                CURLOPT_RETURNTRANSFER => true, CURLOPT_FOLLOWLOCATION => true, CURLOPT_MAXREDIRS => 3,
                CURLOPT_TIMEOUT => $this->timeout, CURLOPT_CONNECTTIMEOUT => 15, CURLOPT_IPRESOLVE => CURL_IPRESOLVE_V4, CURLOPT_USERAGENT => 'Mozilla/5.0 (compatible; VanmeyaImages/1.0)',
                CURLOPT_ENCODING => '', CURLOPT_HTTPHEADER => ['Accept: image/webp,image/*,*/*;q=0.8', 'Referer: https://www.google.com/'],
            ]);
            curl_multi_add_handle($mh, $ch);
            $handles[$k] = $ch;
        }
        do {
            $status = curl_multi_exec($mh, $active);
            if ($active) curl_multi_select($mh, 1.0);
        } while ($active && $status === CURLM_OK);
        $errs = [];
        while (($info = curl_multi_info_read($mh)) !== false) {
            if ($info['result'] !== 0) $errs[(int)$info['handle']] = $info['result'];
        }
        $out = [];
        foreach ($handles as $k => $ch) {
            $body = curl_multi_getcontent($ch);
            $en = isset($errs[(int)$ch]) ? $errs[(int)$ch] : curl_errno($ch);
            $em = $en ? (function_exists('curl_strerror') ? curl_strerror($en) : (string)$en) : '';
            $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
            $out[$k] = [($code === 200 && is_string($body) && $body !== '') ? $body : null, (int)$code, $em !== '' ? $em : curl_error($ch)];
            curl_multi_remove_handle($mh, $ch);
            curl_close($ch);
        }
        curl_multi_close($mh);
        return $out;
    }

    /** Plain-words check of one address: what happened and why. Used by the Test button. */
    public static function diagnose($url) {
        if (!function_exists('curl_init')) return 'curl is not installed on this hosting.';
        $ch = curl_init($url);
        curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER => true, CURLOPT_FOLLOWLOCATION => true, CURLOPT_MAXREDIRS => 3, CURLOPT_TIMEOUT => 25, CURLOPT_CONNECTTIMEOUT => 15,
            CURLOPT_IPRESOLVE => CURL_IPRESOLVE_V4, CURLOPT_USERAGENT => 'Mozilla/5.0 (compatible; VanmeyaImages/1.0)', CURLOPT_HTTPHEADER => ['Referer: https://www.google.com/']]);
        $body = curl_exec($ch);
        $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $msg = curl_error($ch); $no = curl_errno($ch); $ip = curl_getinfo($ch, CURLINFO_PRIMARY_IP); $t = round(curl_getinfo($ch, CURLINFO_TOTAL_TIME), 1);
        curl_close($ch);
        $host = parse_url($url, PHP_URL_HOST);
        return $host . ': answer ' . $code . ', ' . (is_string($body) ? strlen($body) : 0) . ' bytes, ' . $t . 's, server ' . ($ip ?: 'not reached') . ($no ? ', error ' . $no . ' (' . self::net_error($no) . ': ' . $msg . ')' : '');
    }

    /** Check, resize and save one photo. Returns '' on success, or a reason word. */
    public function save_public($it, $body) { return $this->save($it, $body); }

    private function save($it, $body) {
        if (strlen($body) < 2000) return 'too_small';
        $im = @imagecreatefromstring($body);
        if (!$im) return 'not_an_image';
        $w = imagesx($im); $h = imagesy($im);
        if ($w < $this->min_side || $h < $this->min_side) { imagedestroy($im); return 'too_small'; }
        if ($w > $this->max_width) {
            $nh = (int)round($h * $this->max_width / $w);
            $re = imagescale($im, $this->max_width, $nh);
            imagedestroy($im);
            if (!$re) return 'resize';
            $im = $re;
        }
        imagepalettetotruecolor($im);
        $folder = $it['place_id'];
        $dir = $this->dir . '/' . $folder;
        if (!is_dir($dir) && !@mkdir($dir, 0755, true)) { imagedestroy($im); return 'folder'; }
        $tmp = $dir . '/' . $it['idx'] . '.tmp';
        $ok = imagewebp($im, $tmp, $this->quality);
        imagedestroy($im);
        if (!$ok || !@rename($tmp, $dir . '/' . $it['idx'] . '.webp')) { @unlink($tmp); return 'write'; }
        $this->remember($folder, $it['idx'], $it['url']);
        return '';
    }
}
