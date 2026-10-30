<?php
/**
 * Plugin Name: Vanmeya Restaurant Guide
 * Description: Downloads restaurant photos from the images database into one folder per restaurant, and serves the restaurant guide pages built by the Vanmeya site builder.
 * Version: 1.0.4
 * Requires PHP: 7.4
 * License: Proprietary, all rights reserved
 */
if (!defined('ABSPATH')) { exit; }

require_once __DIR__ . '/includes/class-vanmeya-core.php';
require_once __DIR__ . '/includes/class-vanmeya-wp.php';

register_activation_hook(__FILE__, ['Vanmeya_WP', 'activate']);
register_deactivation_hook(__FILE__, ['Vanmeya_WP', 'deactivate']);
Vanmeya_WP::init();
