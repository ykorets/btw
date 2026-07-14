from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "site"
INDEX = "\n".join(
    path.read_text()
    for path in (SITE / "src" / "components" / "home").glob("*.astro")
)


def test_main_repo_has_no_second_data_export():
    data_dir = SITE / "data"
    assert not data_dir.exists() or not any(path.is_file() for path in data_dir.rglob("*"))


def test_verified_ui_has_no_known_stale_numbers():
    forbidden = [
        "+174 obs.",
        "Fleet remainder",
        "6× Solar Titan 350",
        "Williams Socrates South</b>",
        "−9 mo",
    ]
    for value in forbidden:
        assert value not in INDEX


def test_site_does_not_fake_subscription_success():
    assert "Check your inbox to confirm" not in INDEX
    assert "mailto:hello@behindthewatt.com" in INDEX


def test_map_points_have_keyboard_contract():
    assert '.attr("tabindex",0).attr("role","button")' in INDEX
    assert '.on("keydown"' in INDEX


def test_research_placeholders_are_not_dead_links():
    assert '<a class="post" href="#">' not in INDEX


def test_astro_builds_dossiers_from_mirror():
    workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text()
    route = (SITE / "src" / "pages" / "facility" / "[slug]" / "index.astro").read_text()
    sitemap = (SITE / "src" / "pages" / "sitemap.xml.ts").read_text()
    assert "ref: mirror" in workflow
    assert "npm run build" in workflow
    assert "BTW_DATA_DIR" in workflow
    assert "getStaticPaths" in route
    assert "loadMirror" in route
    assert "facility" in sitemap


def test_site_is_astro_and_keeps_public_urls():
    package = (SITE / "package.json").read_text()
    config = (SITE / "astro.config.mjs").read_text()
    assert '"astro"' in package
    assert 'site: "https://behindthewatt.com"' in config
    assert 'trailingSlash: "always"' in config
    assert not (SITE / "index.html").exists()
    assert not (SITE / "build.py").exists()
    assert not (SITE / "src" / "content").exists() or not any(
        (SITE / "src" / "content").iterdir()
    )


def test_home_and_dossiers_are_real_astro_components():
    home = SITE / "src" / "components" / "home"
    facility = SITE / "src" / "components" / "facility"
    assert len(list(home.glob("*.astro"))) >= 15
    assert len(list(facility.glob("*.astro"))) >= 5
    assert "set:html={body}" not in (SITE / "src" / "pages" / "index.astro").read_text()
    assert "renderFacility" not in (
        SITE / "src" / "pages" / "facility" / "[slug]" / "index.astro"
    ).read_text()


def test_announced_capacity_is_separate_from_verified_fleet():
    publisher = (ROOT / "engine" / "src" / "btw_engine" / "publish.py").read_text()
    component = (SITE / "src" / "components" / "home" / "AnnouncedCapacity.astro").read_text()
    hero = (SITE / "src" / "components" / "home" / "Hero.astro").read_text()
    mirror = (SITE / "src" / "lib" / "mirror.ts").read_text()
    workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text()
    assert "announcements.json" in publisher
    assert "--announcements-only" in publisher
    assert "third_party_reported_not_btw_verified" in publisher
    assert "not BTW-verified operating capacity" in component
    assert "What the 143 GW" not in component
    assert "74-project EIP inventory" not in hero
    assert "reported_gw: null" in mirror
    assert "options: [all, announcements]" in workflow
    assert "90,0,600" not in INDEX
