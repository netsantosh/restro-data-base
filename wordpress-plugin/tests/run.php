<?php
// Command line test of the downloader: php tests/run.php
// Starts a tiny web server with good, tiny and broken images, then checks what the plugin keeps.
$base = sys_get_temp_dir() . '/vanmeya_test_' . getmypid();
mkdir("$base/srv", 0777, true);
foreach ([['a.jpg', 1200, 900], ['b.jpg', 640, 480]] as [$n, $w, $h]) {
    $im = imagecreatetruecolor($w, $h);
    for ($i = 0; $i < 40; $i++) imagefilledrectangle($im, rand(0, $w), rand(0, $h), rand(0, $w), rand(0, $h), imagecolorallocate($im, rand(0, 255), rand(0, 255), rand(0, 255)));
    imagejpeg($im, "$base/srv/$n", 92);
}
imagejpeg(imagecreatetruecolor(64, 64), "$base/srv/logo.jpg");
file_put_contents("$base/srv/bad.jpg", str_repeat('notanimage', 500));
$port = 8800 + getmypid() % 100;
$srv = proc_open(['php', '-S', "127.0.0.1:$port", '-t', "$base/srv"], [1 => ['file', '/dev/null', 'w'], 2 => ['file', '/dev/null', 'w']], $p);
usleep(700000);
$u = "http://127.0.0.1:$port";
file_put_contents("$base/t.csv", "\xEF\xBB\xBFplace_id,name,photo_1,photo_2,photo_3,photo_4\nChIJabc-_1,One,$u/a.jpg,$u/b.jpg,$u/missing.jpg,$u/logo.jpg\nChIJ../evil,Two,$u/bad.jpg,,,\n");
require __DIR__ . '/../vanmeya-restaurant-guide/includes/class-vanmeya-core.php';
[$rows, $err] = Vanmeya_Csv::read("$base/t.csv");
$st = new Vanmeya_Store_Memory; $im = new Vanmeya_Images("$base/out", $st);
$im->enqueue($rows);
for ($i = 0; $i < 8; $i++) { if (!$im->step(8)['taken']) break; }
$c = $st->counts();
$ok = $err === '' && count($rows) === 2 && $c['done'] === 2 && $c['failed'] === 3
    && is_file("$base/out/ChIJabc-_1/1.webp") && is_file("$base/out/ChIJabc-_1/2.webp")
    && !is_file("$base/out/ChIJabc-_1/4.webp") && !file_exists("$base/evil") && !file_exists("$base/out/evil");
proc_terminate($srv);
echo $ok ? "PASS\n" : "FAIL " . json_encode($c) . "\n";
exit($ok ? 0 : 1);
