use base64::Engine as _;
use serde::Serialize;
use std::io::{Cursor, Read};
use std::{
    collections::{HashMap, HashSet},
    fs,
    path::{Path, PathBuf},
};
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct App {
    pub id: String,
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub generic_name: Option<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub keywords: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub icon: Option<String>,
    #[serde(skip)]
    pub icon_key: Option<String>,
    #[serde(skip)]
    pub desktop_path: Option<PathBuf>,
    #[serde(skip)]
    pub try_exec: Option<String>,
    #[serde(skip)]
    pub argv: Vec<String>,
    #[serde(skip)]
    pub terminal: bool,
}
// Desktop Exec is a tokenized command line, never a shell string. Field codes
// requiring a file/URL are removed, known metadata codes are expanded.
pub fn parse_desktop(id: &str, text: &str, path: &Path) -> Option<App> {
    let mut fields = HashMap::new();
    let mut section = false;
    for line in text.lines() {
        let line = line.trim();
        if line.starts_with('[') {
            section = line == "[Desktop Entry]";
            continue;
        }
        if section && !line.starts_with('#') {
            if let Some((key, value)) = line.split_once('=') {
                fields.insert(key.trim(), value.trim());
            }
        }
    }
    if fields.get("Type") != Some(&"Application")
        || fields.get("Hidden") == Some(&"true")
        || fields.get("NoDisplay") == Some(&"true")
    {
        return None;
    }
    if fields
        .get("OnlyShowIn")
        .is_some_and(|s| !s.split(';').any(|e| e.eq_ignore_ascii_case("Hyprland")))
    {
        return None;
    }
    if fields
        .get("NotShowIn")
        .is_some_and(|s| s.split(';').any(|e| e.eq_ignore_ascii_case("Hyprland")))
    {
        return None;
    }
    let name = fields.get("Name")?.to_string();
    if name.is_empty()
        || name.contains('\0')
        || name.chars().count() > 100
        || id.len() > 256
        || id.contains('\0')
    {
        return None;
    }
    let exec = fields.get("Exec")?;
    if exec.len() > 8192 || exec.contains('\0') {
        return None;
    }
    let tokens = tokenize(exec)?;
    if tokens.len() > 128 {
        return None;
    }
    let mut argv = Vec::new();
    for token in tokens {
        match token.as_str() {
            "%f" | "%F" | "%u" | "%U" => {}
            "%i" => {
                if let Some(icon) = fields.get("Icon") {
                    argv.extend(["--icon".into(), icon.to_string()]);
                }
            }
            "%c" => argv.push(name.clone()),
            "%k" => argv.push(path.to_string_lossy().into_owned()),
            _ => {
                let token = token.replace("%%", "\u{1}");
                if token.contains('%') {
                    return None;
                }
                argv.push(token.replace('\u{1}', "%"));
            }
        }
    }
    if argv.is_empty() || argv[0].starts_with('-') {
        return None;
    }
    Some(App {
        id: id.into(),
        name,
        generic_name: fields
            .get("GenericName")
            .filter(|s| !s.is_empty())
            .map(|s| s.chars().take(160).collect()),
        keywords: fields
            .get("Keywords")
            .map(|s| {
                s.split(';')
                    .filter(|s| !s.is_empty())
                    .take(16)
                    .map(|s| s.chars().take(80).collect())
                    .collect()
            })
            .unwrap_or_default(),
        icon: None,
        icon_key: fields
            .get("Icon")
            .filter(|s| s.len() <= 512 && !s.contains('\0'))
            .map(|s| s.to_string()),
        desktop_path: None,
        try_exec: fields.get("TryExec").map(|s| s.to_string()),
        argv,
        terminal: fields.get("Terminal") == Some(&"true"),
    })
}
pub fn tokenize(exec: &str) -> Option<Vec<String>> {
    let mut out = Vec::new();
    let mut word = String::new();
    let mut quoted = false;
    let mut escaped = false;
    for c in exec.chars() {
        if escaped {
            word.push(c);
            escaped = false;
            continue;
        }
        match c {
            '\\' => escaped = true,
            '"' => quoted = !quoted,
            c if c.is_whitespace() && !quoted => {
                if !word.is_empty() {
                    out.push(std::mem::take(&mut word));
                }
            }
            _ => word.push(c),
        }
    }
    if quoted || escaped {
        return None;
    }
    if !word.is_empty() {
        out.push(word);
    }
    Some(out)
}
pub const MAX_APPS: usize = 256;
pub const MAX_ICON_BYTES: usize = 24 * 1024;
pub const TOTAL_ICON_BYTES: usize = 2 * 1024 * 1024;

fn read_bounded(path: &Path, max: usize) -> Option<Vec<u8>> {
    let file = fs::File::open(path).ok()?;
    if !file.metadata().ok()?.is_file() || file.metadata().ok()?.len() > max as u64 {
        return None;
    }
    let mut data = Vec::new();
    file.take(max as u64 + 1).read_to_end(&mut data).ok()?;
    (data.len() <= max).then_some(data)
}
fn executable(program: &str, search: &[PathBuf]) -> bool {
    use std::os::unix::fs::PermissionsExt;
    let valid = |p: &Path| {
        fs::metadata(p).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
    };
    let path = Path::new(program);
    if path.is_absolute() {
        return valid(path);
    }
    if program.contains('/') {
        return false;
    }
    search.iter().any(|root| valid(&root.join(program)))
}
fn search_path() -> Vec<PathBuf> {
    std::env::var_os("PATH")
        .map(|s| {
            std::env::split_paths(&s)
                .filter(|p| p.is_absolute())
                .take(32)
                .collect()
        })
        .unwrap_or_default()
}
impl App {
    /// Re-read only the indexed entry. A removed, hidden or changed entry cannot
    /// silently launch an old plan or replace the chosen ID with a new command.
    pub fn validate_installed(&self) -> Result<(), String> {
        let Some(path) = &self.desktop_path else {
            return Ok(());
        };
        let data = read_bounded(path, 64 * 1024).ok_or("application is not installed")?;
        let text = std::str::from_utf8(&data).map_err(|_| "application changed")?;
        let current = parse_desktop(&self.id, text, path).ok_or("application changed")?;
        if current.argv != self.argv || current.terminal != self.terminal {
            return Err("application changed".into());
        }
        let search = search_path();
        if !executable(&self.argv[0], &search)
            || current
                .try_exec
                .as_ref()
                .is_some_and(|p| !executable(p, &search))
        {
            return Err("unavailable executable".into());
        }
        Ok(())
    }
}

/// Decode only a bounded PNG and return a freshly encoded, at-most-64px image.
/// SVGs, arbitrary URLs, files and executable data never cross the IPC boundary.
pub fn icon_png(path: &Path) -> Option<Vec<u8>> {
    if path.extension()?.to_str()? != "png" {
        return None;
    }
    let data = read_bounded(path, 4 * 1024 * 1024)?;
    if data.get(..8)? != b"\x89PNG\r\n\x1a\n" {
        return None;
    }
    let mut decoder = png::Decoder::new(Cursor::new(data));
    decoder.set_limits(png::Limits {
        bytes: 80 * 1024 * 1024,
    });
    decoder.set_transformations(png::Transformations::EXPAND | png::Transformations::STRIP_16);
    let mut reader = decoder.read_info().ok()?;
    let info = reader.info();
    if info.width == 0
        || info.height == 0
        || info.width > 4096
        || info.height > 4096
        || info.animation_control.is_some()
    {
        return None;
    }
    let mut buffer = vec![0; reader.output_buffer_size()?];
    let frame = reader.next_frame(&mut buffer).ok()?;
    let channels = match frame.color_type {
        png::ColorType::Rgba => 4,
        png::ColorType::Rgb => 3,
        png::ColorType::GrayscaleAlpha => 2,
        png::ColorType::Grayscale => 1,
        _ => return None,
    };
    let scale = 64.0 / frame.width.max(frame.height).max(64) as f64;
    let width = (frame.width as f64 * scale).round().max(1.0) as u32;
    let height = (frame.height as f64 * scale).round().max(1.0) as u32;
    let mut pixels = Vec::with_capacity((width * height * 4) as usize);
    for y in 0..height {
        for x in 0..width {
            let source = (((y * frame.height / height) * frame.width + x * frame.width / width)
                as usize)
                * channels;
            let p = &buffer[source..source + channels];
            match channels {
                4 => pixels.extend_from_slice(p),
                3 => pixels.extend_from_slice(&[p[0], p[1], p[2], 255]),
                2 => pixels.extend_from_slice(&[p[0], p[0], p[0], p[1]]),
                _ => pixels.extend_from_slice(&[p[0], p[0], p[0], 255]),
            }
        }
    }
    let mut result = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut result, width, height);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        encoder
            .write_header()
            .ok()?
            .write_image_data(&pixels)
            .ok()?;
    }
    (result.len() <= MAX_ICON_BYTES).then_some(result)
}

/// Render a bounded, self-contained installed SVG through the already linked
/// pixbuf/librsvg decoder. No URI base is supplied, and external references are
/// rejected before decoding. IPC receives only freshly encoded PNG pixels.
#[cfg(feature = "desktop")]
pub fn icon_svg(path: &Path) -> Option<Vec<u8>> {
    use gtk::gdk_pixbuf::prelude::*;
    let data = read_bounded(path, 512 * 1024)?;
    let text = std::str::from_utf8(&data).ok()?;
    let doc = roxmltree::Document::parse(text).ok()?;
    if doc.root_element().tag_name().name() != "svg" || doc.descendants().count() > 20000 {
        return None;
    }
    let self_contained = |value: &str| {
        let lower = value.to_ascii_lowercase();
        if lower.contains("@import") || lower.contains('\\') {
            return false;
        }
        lower.match_indices("url(").all(|(offset, _)| {
            lower[offset + 4..]
                .trim_start()
                .trim_start_matches(['\'', '"'])
                .starts_with('#')
        })
    };
    for node in doc.descendants() {
        if node.attributes().any(|attr| {
            (attr.name() == "href" && !attr.value().starts_with('#'))
                || !self_contained(attr.value())
        }) || node.text().is_some_and(|value| !self_contained(value))
        {
            return None;
        }
    }
    let loader = gtk::gdk_pixbuf::PixbufLoader::with_type("svg").ok()?;
    loader.set_size(64, 64);
    loader.write(&data).ok()?;
    loader.close().ok()?;
    let pixbuf = loader.pixbuf()?;
    if pixbuf.width() > 64 || pixbuf.height() > 64 {
        return None;
    }
    let png = pixbuf.save_to_bufferv("png", &[]).ok()?;
    (png.len() <= MAX_ICON_BYTES).then_some(png)
}
#[cfg(not(feature = "desktop"))]
pub fn icon_svg(_path: &Path) -> Option<Vec<u8>> {
    None
}
fn resolve_icon(path: &Path) -> Option<Vec<u8>> {
    match path.extension()?.to_str()? {
        "png" => icon_png(path),
        "svg" => icon_svg(path),
        _ => None,
    }
}

pub fn collect(home: &Path) -> Vec<App> {
    let data_home = std::env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .unwrap_or_else(|| home.join(".local/share"));
    let dirs = std::env::var("XDG_DATA_DIRS")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "/usr/local/share:/usr/share".into());
    let mut roots = vec![data_home];
    roots.extend(
        dirs.split(':')
            .map(PathBuf::from)
            .filter(|p| p.is_absolute())
            .take(15),
    );
    let mut apps = collect_roots(&roots, &search_path());
    let mut icon_roots = vec![home.join(".icons")];
    for root in &roots {
        icon_roots.extend([root.join("icons"), root.join("pixmaps")]);
    }
    attach_icons(&mut apps, &icon_roots);
    apps
}

/// Deterministic, bounded XDG discovery. Record IDs before filtering so Hidden
/// and NoDisplay in a higher-precedence root mask lower-precedence entries.
pub fn collect_roots(roots: &[PathBuf], search: &[PathBuf]) -> Vec<App> {
    let mut seen = HashSet::new();
    let mut result = Vec::new();
    for root in roots.iter().take(16) {
        let root = root.join("applications");
        let mut paths = ignore::WalkBuilder::new(&root)
            .hidden(false)
            .git_ignore(false)
            .git_exclude(false)
            .git_global(false)
            .ignore(false)
            .max_depth(Some(4))
            .sort_by_file_path(|a, b| a.cmp(b))
            .build()
            .flatten()
            .take(5000)
            .map(|e| e.into_path())
            .collect::<Vec<_>>();
        paths.sort();
        for path in paths {
            if path.extension().is_none_or(|e| e != "desktop") {
                continue;
            }
            let Some(id) = path
                .strip_prefix(&root)
                .ok()
                .map(|p| p.to_string_lossy().replace('/', "-"))
            else {
                continue;
            };
            if !seen.insert(id.clone()) {
                continue;
            }
            let Some(data) = read_bounded(&path, 64 * 1024) else {
                continue;
            };
            let Ok(text) = std::str::from_utf8(&data) else {
                continue;
            };
            let Some(mut app) = parse_desktop(&id, text, &path) else {
                continue;
            };
            if !executable(&app.argv[0], search) {
                continue;
            }
            if app
                .try_exec
                .as_ref()
                .is_some_and(|p| !executable(p, search))
            {
                continue;
            }
            app.desktop_path = Some(path);
            result.push(app);
        }
    }
    result.sort_by(|a, b| {
        a.name
            .to_lowercase()
            .cmp(&b.name.to_lowercase())
            .then(a.id.cmp(&b.id))
    });
    result.truncate(MAX_APPS);
    result
}

pub fn attach_icons(apps: &mut [App], roots: &[PathBuf]) {
    let wanted: HashSet<String> = apps.iter().filter_map(|a| a.icon_key.clone()).collect();
    let mut candidates: HashMap<String, Vec<PathBuf>> = HashMap::new();
    // Resolve standard hicolor app locations before the bounded theme walk.
    // Large optional themes must not crowd a known installed icon out of the
    // scan budget. Lookup is bounded by the 256 app IDs and fixed size list.
    for key in &wanted {
        if Path::new(key).components().count() != 1 {
            continue;
        }
        let name = key
            .strip_suffix(".png")
            .or_else(|| key.strip_suffix(".svg"))
            .unwrap_or(key);
        for root in roots.iter().take(33) {
            let mut paths = vec![
                root.join(format!("{name}.png")),
                root.join(format!("{name}.svg")),
                root.join(format!("hicolor/scalable/apps/{name}.svg")),
            ];
            for size in [64, 48, 128, 256, 32, 24, 16] {
                paths.push(root.join(format!("hicolor/{size}x{size}/apps/{name}.png")));
            }
            for path in paths {
                if path.is_file() {
                    candidates.entry(key.clone()).or_default().push(path);
                }
            }
        }
    }
    let mut scanned = 0;
    for root in roots.iter().take(33) {
        for entry in ignore::WalkBuilder::new(root)
            .hidden(false)
            .git_ignore(false)
            .ignore(false)
            .max_depth(Some(7))
            .sort_by_file_path(|a, b| a.cmp(b))
            .build()
            .flatten()
            .take(12000 - scanned)
        {
            scanned += 1;
            let path = entry.path();
            if path.extension().is_none_or(|e| e != "png" && e != "svg") {
                continue;
            }
            let Some(name) = path.file_stem().and_then(|s| s.to_str()) else {
                continue;
            };
            if wanted.contains(name) && candidates.get(name).map_or(0, Vec::len) < 32 {
                candidates.entry(name.into()).or_default().push(path.into());
            }
        }
        if scanned >= 12000 {
            break;
        }
    }
    let mut cache: HashMap<String, Option<Vec<u8>>> = HashMap::new();
    let mut used = 0;
    for app in apps {
        let Some(key) = &app.icon_key else {
            continue;
        };
        let png = cache.entry(key.clone()).or_insert_with(|| {
            if Path::new(key).is_absolute() {
                return resolve_icon(Path::new(key));
            }
            let list = candidates.get_mut(key)?;
            list.sort_by_key(|p| {
                let value = p.to_string_lossy();
                if value.contains("/64x64/") {
                    0
                } else if value.contains("/48x48/") {
                    1
                } else {
                    2
                }
            });
            list.iter().find_map(|p| resolve_icon(p))
        });
        if let Some(data) = png {
            if used + data.len() <= TOTAL_ICON_BYTES {
                used += data.len();
                app.icon = Some(format!(
                    "data:image/png;base64,{}",
                    base64::engine::general_purpose::STANDARD.encode(data)
                ));
            }
        }
    }
}
