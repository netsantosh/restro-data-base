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
    public function finish($id, $status, $bump_tries) {
        $this->items[$id]['status'] = $status;
        if ($bump_tries) $this->items[$id]['tries']++;
    }
    public function counts() {
        $c = ['pending' => 0, 'done' => 0, 'failed' => 0];
        foreach ($this->items as $it) { $c[$it['status']] = ($c[$it['status']] ?? 0) + 1; }
        return $c;
    }
}

class Vanmeya_Images {
    public $dir;            // photo root, one folder per restaurant inside
    public $store;
    public $max_tries = 3;
    public $parallel = 8;
    public $max_width = 800;
    public $quality = 78;
    public $min_side = 200;     // smaller images are logos or icons
    public $timeout = 12;

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
        $items = $this->store->take($batch, $this->max_tries);
        $res = ['ok' => 0, 'failed' => 0, 'retry' => 0, 'taken' => count($items)];
        if (!$items) return $res;
        foreach (array_chunk($items, $this->parallel) as $group) {
            $bodies = $this->fetch_many(array_column($group, 'url'));
            foreach ($group as $k => $it) {
                $body = $bodies[$k];
                $why = $body === null ? 'download' : $this->save($it, $body);
                if ($why === '') { $this->store->finish($it['id'], 'done', false); $res['ok']++; }
                elseif ($why === 'permanent' || $it['tries'] + 1 >= $this->max_tries) { $this->store->finish($it['id'], 'failed', true); $res['failed']++; }
                else { $this->store->finish($it['id'], 'pending', true); $res['retry']++; }
            }
        }
        return $res;
    }

    /** Several downloads at once with curl_multi. Returns an array of bodies (null = failed) in the same order. */
    public function fetch_many($urls) {
        $mh = curl_multi_init();
        $handles = [];
        foreach ($urls as $k => $u) {
            $ch = curl_init($u);
            curl_setopt_array($ch, [
                CURLOPT_RETURNTRANSFER => true, CURLOPT_FOLLOWLOCATION => true, CURLOPT_MAXREDIRS => 3,
                CURLOPT_TIMEOUT => $this->timeout, CURLOPT_CONNECTTIMEOUT => 8, CURLOPT_USERAGENT => 'Mozilla/5.0 (compatible; VanmeyaImages/1.0)',
                CURLOPT_ENCODING => '', CURLOPT_FAILONERROR => true,
            ]);
            curl_multi_add_handle($mh, $ch);
            $handles[$k] = $ch;
        }
        do {
            $status = curl_multi_exec($mh, $active);
            if ($active) curl_multi_select($mh, 1.0);
        } while ($active && $status === CURLM_OK);
        $out = [];
        foreach ($handles as $k => $ch) {
            $body = curl_multi_getcontent($ch);
            $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
            $out[$k] = ($code === 200 && is_string($body) && $body !== '') ? $body : null;
            curl_multi_remove_handle($mh, $ch);
            curl_close($ch);
        }
        curl_multi_close($mh);
        return $out;
    }

    /** Check, resize and save one photo. Returns '' on success, 'permanent' for a bad image, or another word for "try again". */
    private function save($it, $body) {
        if (strlen($body) < 2000) return 'permanent';
        $im = @imagecreatefromstring($body);
        if (!$im) return 'permanent';
        $w = imagesx($im); $h = imagesy($im);
        if ($w < $this->min_side || $h < $this->min_side) { imagedestroy($im); return 'permanent'; }
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
