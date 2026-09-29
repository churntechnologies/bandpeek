//! User preferences. Small JSON file next to the history database.
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

/// Retention choices offered in Settings. Every choice keeps at least the
/// longest selectable range (30 days).
pub const RETENTION_CHOICES: [u32; 4] = [30, 90, 180, 365];

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Appearance {
    #[default]
    System,
    Light,
    Dark,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Units {
    /// GB: base 1000.
    #[default]
    Decimal,
    /// GiB: base 1024.
    Binary,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum MenuBarDisplay {
    #[default]
    SpeedsOnly,
    IconAndSpeeds,
    IconOnly,
}

impl MenuBarDisplay {
    /// Content width plus the brand's six-point padding on each side.
    pub fn item_width(self, measured_rates_width: f64) -> f64 {
        12.0 + match self {
            Self::SpeedsOnly => measured_rates_width,
            Self::IconAndSpeeds => 14.0 + 4.0 + measured_rates_width,
            Self::IconOnly => 16.0,
        }
    }
}

/// Menu-bar rates always use decimal units and one fractional digit.
pub fn format_menu_rate(rate: f64) -> String {
    let rate = if rate.is_finite() && rate > 0.0 {
        rate
    } else {
        0.0
    };
    let units = ["B/s", "KB/s", "MB/s", "GB/s"];
    let mut value = rate;
    let mut index = 0;
    while value >= 999.95 && index < 3 {
        value /= 1000.0;
        index += 1;
    }
    format!("{value:.1} {}", units[index])
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(default)]
pub struct Settings {
    pub appearance: Appearance,
    pub units: Units,
    pub retention_days: u32,
    pub menu_bar_display: MenuBarDisplay,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            appearance: Appearance::System,
            units: Units::Decimal,
            retention_days: crate::db::DEFAULT_RETENTION_DAYS,
            menu_bar_display: MenuBarDisplay::SpeedsOnly,
        }
    }
}

impl Settings {
    pub fn default_path() -> PathBuf {
        if let Ok(path) = std::env::var("BANDPEEK_SETTINGS_PATH") {
            if !path.trim().is_empty() {
                return PathBuf::from(path.trim());
            }
        }
        crate::db::HistoryStore::default_db_path()
            .parent()
            .map(|dir| dir.join("settings.json"))
            .unwrap_or_else(|| PathBuf::from("settings.json"))
    }

    /// Unknown, missing or corrupt files fall back to defaults instead of failing startup.
    pub fn load(path: &Path) -> Self {
        std::fs::read(path)
            .ok()
            .and_then(|bytes| serde_json::from_slice::<Settings>(&bytes).ok())
            .unwrap_or_default()
            .normalized()
    }

    pub fn normalized(mut self) -> Self {
        if !RETENTION_CHOICES.contains(&self.retention_days) {
            self.retention_days = crate::db::DEFAULT_RETENTION_DAYS;
        }
        self
    }

    /// Write-then-rename so an interrupted save never leaves a truncated file.
    pub fn save(&self, path: &Path) -> std::io::Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let tmp = path.with_extension("json.tmp");
        std::fs::write(&tmp, serde_json::to_vec_pretty(self)?)?;
        std::fs::rename(tmp, path)
    }
}

/// Formats bytes (or bytes/second) exactly as the design specifies:
/// smallest unit KB/KiB, >=100 → 0 decimals, >=10 → 1, otherwise 2.
pub fn format_bytes(bytes: f64, units: Units) -> (String, &'static str) {
    let (base, names) = match units {
        Units::Decimal => (1000f64, ["B", "KB", "MB", "GB", "TB"]),
        Units::Binary => (1024f64, ["B", "KiB", "MiB", "GiB", "TiB"]),
    };
    if !(bytes.is_finite() && bytes > 0.0) {
        return ("0".into(), names[1]);
    }
    let exponent = (bytes.ln() / base.ln()).floor().clamp(1.0, 4.0);
    let value = bytes / base.powf(exponent);
    let digits = if value >= 100.0 {
        0
    } else if value >= 10.0 {
        1
    } else {
        2
    };
    (format!("{value:.digits$}"), names[exponent as usize])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_like_the_design() {
        let f = |b: f64, u| {
            let (n, unit) = format_bytes(b, u);
            format!("{n} {unit}")
        };
        assert_eq!(f(0.0, Units::Decimal), "0 KB");
        assert_eq!(f(1_840_000_000.0, Units::Decimal), "1.84 GB");
        assert_eq!(f(36_000_000.0, Units::Decimal), "36.0 MB");
        assert_eq!(f(142_000_000.0, Units::Decimal), "142 MB");
        assert_eq!(f(500.0, Units::Decimal), "0.50 KB");
        assert_eq!(f(1024.0 * 1024.0, Units::Binary), "1.00 MiB");
        assert_eq!(f(5e15, Units::Decimal), "5000 TB");
        assert_eq!(f(f64::NAN, Units::Binary), "0 KiB");
    }

    #[test]
    fn settings_round_trip_and_recover() {
        let dir = std::env::temp_dir().join(format!("bandpeek_settings_{}", std::process::id()));
        let path = dir.join("settings.json");
        let _ = std::fs::remove_file(&path);
        assert_eq!(Settings::load(&path), Settings::default());

        let custom = Settings {
            appearance: Appearance::Dark,
            units: Units::Binary,
            retention_days: 365,
            menu_bar_display: MenuBarDisplay::IconAndSpeeds,
        };
        custom.save(&path).unwrap();
        assert_eq!(Settings::load(&path), custom);

        std::fs::write(&path, b"{not json").unwrap();
        assert_eq!(Settings::load(&path), Settings::default());

        std::fs::write(&path, br#"{"retention_days": 7, "units": "binary"}"#).unwrap();
        let partial = Settings::load(&path);
        assert_eq!(partial.retention_days, crate::db::DEFAULT_RETENTION_DAYS);
        assert_eq!(partial.units, Units::Binary);
        assert_eq!(partial.appearance, Appearance::System);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn menu_modes_persist_and_old_settings_default_to_speeds() {
        let old: Settings = serde_json::from_str(r#"{"units":"binary"}"#).unwrap();
        assert_eq!(old.menu_bar_display, MenuBarDisplay::SpeedsOnly);
        for mode in [
            MenuBarDisplay::SpeedsOnly,
            MenuBarDisplay::IconAndSpeeds,
            MenuBarDisplay::IconOnly,
        ] {
            let settings = Settings {
                menu_bar_display: mode,
                ..Settings::default()
            };
            let encoded = serde_json::to_vec(&settings).unwrap();
            assert_eq!(
                serde_json::from_slice::<Settings>(&encoded).unwrap(),
                settings
            );
        }
    }

    #[test]
    fn menu_rates_and_fixed_layout() {
        for (input, output) in [
            (0.0, "0.0 B/s"),
            (999.0, "999.0 B/s"),
            (14_300.0, "14.3 KB/s"),
            (888_800_000.0, "888.8 MB/s"),
            (1_700_000_000.0, "1.7 GB/s"),
            (f64::NAN, "0.0 B/s"),
            (-1.0, "0.0 B/s"),
        ] {
            assert_eq!(format_menu_rate(input), output);
        }
        // Rate values do not enter the layout calculation. Measurement does.
        assert_eq!(MenuBarDisplay::SpeedsOnly.item_width(70.0), 82.0);
        assert_eq!(MenuBarDisplay::IconAndSpeeds.item_width(70.0), 100.0);
        assert_eq!(MenuBarDisplay::IconOnly.item_width(70.0), 28.0);
    }
}
