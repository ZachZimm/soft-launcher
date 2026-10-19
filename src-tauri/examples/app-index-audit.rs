//! Read-only public-fixture audit. Never collects window/clipboard or launches.
use base64::Engine as _;
use soft_launcher::collectors::apps;
fn main() {
    let home = std::path::PathBuf::from(std::env::var_os("HOME").unwrap());
    let start = std::time::Instant::now();
    let index = apps::collect(&home);
    if std::env::args().any(|arg| arg == "--matching-index") {
        let metadata = index.iter().map(|app| serde_json::json!({"id":app.id,"name":app.name,"genericName":app.generic_name,"keywords":app.keywords,"icon":app.icon.as_ref().map(|_|"present")})).collect::<Vec<_>>();
        println!("{}", serde_json::to_string(&metadata).unwrap());
        return;
    }
    let competitors = index.iter().filter(|app| ["Brave", "Brogue CE", "Broken Arrow", "Chromium", "Firefox"].contains(&app.name.as_str())).map(|app| serde_json::json!({"id":app.id,"name":app.name,"genericName":app.generic_name,"keywords":app.keywords})).collect::<Vec<_>>();
    let brave = index.iter().find(|a| a.id == "brave-browser.desktop").map(|app| {
        let icon = app.icon.as_ref().and_then(|data| base64::engine::general_purpose::STANDARD.decode(data.strip_prefix("data:image/png;base64,")?).ok());
        let dimensions = icon.as_ref().and_then(|data| png::Decoder::new(std::io::Cursor::new(data)).read_info().ok().map(|reader| [reader.info().width, reader.info().height]));
        serde_json::json!({"id":app.id,"name":app.name,"genericName":app.generic_name,"hasIcon":app.icon.is_some(),"pngBytes":icon.as_ref().map(Vec::len),"iconDimensions":dimensions,"validInstalledEntry":app.validate_installed().is_ok()})
    });
    println!("{}", serde_json::to_string_pretty(&serde_json::json!({"readOnly":true,"desktopInput":false,"inferenceRequests":0,"indexCount":index.len(),"icons":index.iter().filter(|a|a.icon.is_some()).count(),"totalIconPayloadBytes":index.iter().filter_map(|a|a.icon.as_ref()).map(String::len).sum::<usize>(),"collectionMs":start.elapsed().as_millis(),"publicFixture":brave,"publicCompetitors":competitors})).unwrap());
}
