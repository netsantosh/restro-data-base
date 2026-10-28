# Vanmeya Restaurant Guide (WordPress plugin)

What it does:
1. **Photos.** You upload `images_database.csv` (made by the GitHub "photos" workflow). The plugin downloads each restaurant's
   verified photos and stores them in one folder per restaurant: `wp-content/uploads/vanmeya/photos/<place_id>/1.webp, 2.webp, ...`
   Photos are resized to 800 px wide WebP files, outside the Media Library (so WordPress does not make extra copies).
2. **Pages.** You upload `site-for-hosting.zip` (made by the GitHub "hosting-site" workflow). The plugin serves those pages at the
   root of the WordPress site. The pages already point to the photo folders above, so every restaurant page shows its own photos.

## Install on staging
1. WordPress admin, Plugins, Add New, Upload Plugin, choose `vanmeya-restaurant-guide.zip`, Activate.
2. Open the new **Vanmeya** menu. Part 1 shows whether the hosting is ready (WebP, zip, free disk space).
3. Note the photo path shown in part 3 (normally `/wp-content/uploads/vanmeya/photos`).

## Every update
1. GitHub, Actions, **photos**, Run workflow. When it finishes, download `images-database` from the run's Artifacts and unzip it.
2. GitHub, Actions, **hosting-site**, Run workflow. Enter the site address (for staging, the staging address) and keep the photo path.
   When it finishes, download `site-for-hosting` from the Artifacts and unzip it (it contains `site-for-hosting.zip`).
3. Vanmeya, part 2: upload `images_database.csv`, press **Start downloading**, keep the page open until it says Finished.
4. Vanmeya, part 3: upload `site-for-hosting.zip`.

Google photo links expire, so do step 3 soon after step 1.

## Notes
- Staging stays hidden from Google (a "noindex" header and a robots.txt that blocks everything). Untick this in Settings when you go live.
- Large files: put them in `wp-content/uploads/vanmeya/incoming/` with the hosting file manager and tick "use incoming".
- Photos that fail (expired link, too small, not an image) are listed under part 2 and can be retried.
- Unit tests for the downloader are in `tests/` (run with `php tests/run.php`).
