use serde::Serialize;
#[derive(Clone, Debug, Serialize)]
pub struct Clipboard {
    pub kind: String,
    pub head: String,
}
pub fn parse(mime: &[u8], bytes: &[u8], private: bool) -> Clipboard {
    if private {
        return Clipboard {
            kind: "private".into(),
            head: String::new(),
        };
    }
    let mime = String::from_utf8_lossy(mime);
    if !mime.lines().any(|m| m.starts_with("text/")) {
        return Clipboard {
            kind: if mime.contains("image/") {
                "image"
            } else {
                "empty"
            }
            .into(),
            head: String::new(),
        };
    }
    let head: String = String::from_utf8_lossy(bytes)
        .chars()
        .filter(|c| !c.is_control() || matches!(c, '\n' | '\t'))
        .take(200)
        .collect();
    let kind = if head.is_empty() {
        "empty"
    } else if url::Url::parse(head.trim()).is_ok_and(|u| matches!(u.scheme(), "http" | "https")) {
        "url"
    } else {
        "text"
    };
    Clipboard {
        kind: kind.into(),
        head,
    }
}
