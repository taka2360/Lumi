//! Presentation-only locale selection for native Shell surfaces.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Locale {
    Ja,
    En,
}

/// Matches Stage: Japanese tags select Japanese; everything else safely falls back to English.
pub fn resolve_locale(language: Option<&str>) -> Locale {
    let primary = language.unwrap_or_default().split(['-', '_', '.']).next().unwrap_or_default();
    if primary.eq_ignore_ascii_case("ja") {
        Locale::Ja
    } else {
        Locale::En
    }
}

pub fn system_locale() -> Locale {
    #[cfg(target_os = "windows")]
    if let Some(language) = windows_locale() {
        return resolve_locale(Some(&language));
    }

    let language = ["LC_ALL", "LC_MESSAGES", "LANG"]
        .iter()
        .find_map(|name| std::env::var(name).ok().filter(|value| !value.is_empty()));
    resolve_locale(language.as_deref())
}

#[cfg(target_os = "windows")]
fn windows_locale() -> Option<String> {
    use windows_sys::Win32::Globalization::GetUserDefaultLocaleName;

    // Windows documents LOCALE_NAME_MAX_LENGTH as 85 UTF-16 code units.
    let mut buffer = [0_u16; 85];
    // SAFETY: `buffer` is writable for exactly the length passed to Win32.
    let length = unsafe { GetUserDefaultLocaleName(buffer.as_mut_ptr(), buffer.len() as i32) };
    if length <= 1 {
        return None;
    }
    Some(String::from_utf16_lossy(&buffer[..length as usize - 1]))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn japanese_regional_tags_select_japanese() {
        assert_eq!(resolve_locale(Some("ja-JP")), Locale::Ja);
        assert_eq!(resolve_locale(Some("ja_JP.UTF-8")), Locale::Ja);
    }

    #[test]
    fn unsupported_or_missing_tags_fall_back_to_english() {
        assert_eq!(resolve_locale(Some("fr-FR")), Locale::En);
        assert_eq!(resolve_locale(None), Locale::En);
    }
}
