from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata


def normalize_match_text(value: str) -> str:
    cleaned = unicodedata.normalize("NFKD", str(value or ""))
    cleaned = "".join(ch for ch in cleaned if not unicodedata.combining(ch))
    cleaned = cleaned.lower()
    cleaned = re.sub(r"[^a-z0-9\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


@dataclass(frozen=True)
class VisualVocabularyTerm:
    term: str
    weight: float = 1.0
    aliases: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChannelVisualVocabulary:
    key: str
    label: str
    terms: tuple[VisualVocabularyTerm, ...]
    negative_terms: tuple[str, ...] = ()
    negative_aliases: tuple[str, ...] = ()

    def canonical_terms(self) -> list[str]:
        return [str(item.term).strip() for item in self.terms if str(item.term).strip()]


RELIGIOUS_VISUAL_VOCABULARY = ChannelVisualVocabulary(
    key="refugio_da_fe",
    label="Refugio da Fe",
    terms=(
        VisualVocabularyTerm("jesus", 4.6, ("cristo", "jesus christ", "senhor jesus"), ("faith",)),
        VisualVocabularyTerm("cross", 4.5, ("cruz", "wooden cross"), ("symbol",)),
        VisualVocabularyTerm("bible", 4.5, ("biblia", "holy bible", "scripture"), ("scripture",)),
        VisualVocabularyTerm("prayer", 4.4, ("oracao", "orar", "praying", "prayer hands"), ("devotion",)),
        VisualVocabularyTerm("worship", 4.1, ("adoracao", "louvor", "worship service"), ("church",)),
        VisualVocabularyTerm("church", 4.1, ("igreja", "chapel", "cathedral", "temple"), ("place",)),
        VisualVocabularyTerm("faith", 4.0, ("fe", "belief", "trust in god"), ("theme",)),
        VisualVocabularyTerm("gospel", 4.0, ("evangelho", "good news"), ("scripture",)),
        VisualVocabularyTerm("psalm", 3.9, ("salmo", "salmos"), ("scripture",)),
        VisualVocabularyTerm("miracle", 3.9, ("milagre", "miraculous"), ("theme",)),
        VisualVocabularyTerm("holy spirit", 3.9, ("espirito santo",), ("theme",)),
        VisualVocabularyTerm("grace", 3.8, ("graca", "god grace"), ("theme",)),
        VisualVocabularyTerm("mercy", 3.8, ("misericordia",), ("theme",)),
        VisualVocabularyTerm("forgiveness", 3.8, ("perdao", "forgive"), ("theme",)),
        VisualVocabularyTerm("salvation", 3.8, ("salvacao", "redeem", "redemption"), ("theme",)),
        VisualVocabularyTerm("hope", 3.7, ("esperanca", "hopeful"), ("theme",)),
        VisualVocabularyTerm("blessing", 3.7, ("bencao", "blessings"), ("theme",)),
        VisualVocabularyTerm("devotion", 3.7, ("devotional", "quiet time"), ("devotion",)),
        VisualVocabularyTerm("prayer group", 3.7, ("grupo de oracao", "small group prayer"), ("community",)),
        VisualVocabularyTerm("family prayer", 3.7, ("family worship", "prayer at home"), ("community",)),
        VisualVocabularyTerm("christian family", 3.6, ("familia crista", "family faith"), ("community",)),
        VisualVocabularyTerm("community", 3.6, ("comunidade", "fellowship"), ("community",)),
        VisualVocabularyTerm("worship hands", 3.6, ("raised hands", "hands worship"), ("gesture",)),
        VisualVocabularyTerm("hands", 3.6, ("open hands", "folded hands"), ("gesture",)),
        VisualVocabularyTerm("kneeling", 3.6, ("ajoelhado", "kneeling prayer"), ("gesture",)),
        VisualVocabularyTerm("reflection", 3.5, ("reflexao", "reflective"), ("mood",)),
        VisualVocabularyTerm("meditation", 3.5, ("meditacao", "quiet reflection"), ("mood",)),
        VisualVocabularyTerm("comfort", 3.5, ("consolo", "encouragement"), ("theme",)),
        VisualVocabularyTerm("healing", 3.5, ("cura", "restoration"), ("theme",)),
        VisualVocabularyTerm("peace", 3.5, ("paz", "calm"), ("mood",)),
        VisualVocabularyTerm("love", 3.5, ("amor", "compassion"), ("theme",)),
        VisualVocabularyTerm("charity", 3.4, ("caridade", "kindness", "service"), ("community",)),
        VisualVocabularyTerm("kindness", 3.4, ("gentleness", "care"), ("theme",)),
        VisualVocabularyTerm("gratitude", 3.4, ("gratidao", "thankful"), ("theme",)),
        VisualVocabularyTerm("obedience", 3.4, ("obediencia",), ("theme",)),
        VisualVocabularyTerm("discipleship", 3.4, ("discipulado", "disciple"), ("theme",)),
        VisualVocabularyTerm("sermon", 3.4, ("pregacao", "preaching"), ("church",)),
        VisualVocabularyTerm("preacher", 3.4, ("pastor", "pregador"), ("church",)),
        VisualVocabularyTerm("pastor", 3.4, ("shepherd church",), ("church",)),
        VisualVocabularyTerm("altar", 3.4, ("church altar",), ("church",)),
        VisualVocabularyTerm("communion", 3.4, ("ceia", "bread and wine"), ("church",)),
        VisualVocabularyTerm("baptism", 3.4, ("batismo",), ("church",)),
        VisualVocabularyTerm("scripture reading", 3.4, ("reading bible", "bible reading"), ("scripture",)),
        VisualVocabularyTerm("open bible", 3.4, ("bible pages", "bible close up"), ("scripture",)),
        VisualVocabularyTerm("candle", 3.3, ("vela", "candles"), ("object",)),
        VisualVocabularyTerm("sunrise", 3.3, ("amanhecer", "dawn"), ("nature",)),
        VisualVocabularyTerm("sunset", 3.3, ("entardecer",), ("nature",)),
        VisualVocabularyTerm("sky", 3.3, ("heaven sky", "clouds"), ("nature",)),
        VisualVocabularyTerm("mountain", 3.3, ("montanha", "hills"), ("nature",)),
        VisualVocabularyTerm("river", 3.3, ("rio", "water stream"), ("nature",)),
        VisualVocabularyTerm("path", 3.3, ("caminho", "walkway"), ("nature",)),
        VisualVocabularyTerm("light", 3.3, ("luz", "sunlight"), ("nature",)),
        VisualVocabularyTerm("silhouette", 3.3, ("backlit figure",), ("mood",)),
        VisualVocabularyTerm("embrace", 3.2, ("hug", "comfort hug"), ("community",)),
        VisualVocabularyTerm("support", 3.2, ("encorajamento", "helping hands"), ("community",)),
        VisualVocabularyTerm("mother and child", 3.2, ("family embrace",), ("community",)),
        VisualVocabularyTerm("couple praying", 3.2, ("casal orando",), ("community",)),
        VisualVocabularyTerm("young adult praying", 3.2, ("teen prayer",), ("community",)),
        VisualVocabularyTerm("elderly praying", 3.2, ("senior prayer",), ("community",)),
        VisualVocabularyTerm("church crowd", 3.2, ("congregation", "worship crowd"), ("church",)),
        VisualVocabularyTerm("choir", 3.2, ("coral", "gospel choir"), ("church",)),
        VisualVocabularyTerm("service", 3.2, ("church service", "religious service"), ("church",)),
        VisualVocabularyTerm("reading", 3.1, ("study", "book reading"), ("support",)),
        VisualVocabularyTerm("journal", 3.1, ("writing prayer", "notebook"), ("support",)),
        VisualVocabularyTerm("home", 3.1, ("family home", "living room"), ("support",)),
        VisualVocabularyTerm("window light", 3.1, ("soft window light",), ("support",)),
        VisualVocabularyTerm("quiet room", 3.1, ("silent room",), ("support",)),
        VisualVocabularyTerm("encouragement", 3.1, ("uplift", "strength"), ("theme",)),
        VisualVocabularyTerm("trust", 3.1, ("confidence", "reliance"), ("theme",)),
        VisualVocabularyTerm("promise", 3.1, ("promessa",), ("theme",)),
        VisualVocabularyTerm("wisdom", 3.1, ("sabedoria",), ("theme",)),
        VisualVocabularyTerm("humility", 3.1, ("humildade",), ("theme",)),
        VisualVocabularyTerm("patience", 3.1, ("paciencia",), ("theme",)),
        VisualVocabularyTerm("strength", 3.1, ("forca", "resilience"), ("theme",)),
        VisualVocabularyTerm("joy", 3.1, ("alegria", "rejoice"), ("theme",)),
        VisualVocabularyTerm("victory", 3.1, ("vitoria", "overcome"), ("theme",)),
        VisualVocabularyTerm("resurrection", 3.1, ("ressurreicao", "empty tomb"), ("theme",)),
        VisualVocabularyTerm("shepherd", 3.1, ("pastor de ovelhas", "sheep field"), ("biblical",)),
        VisualVocabularyTerm("bread", 3.0, ("pao",), ("biblical",)),
        VisualVocabularyTerm("desert", 3.0, ("wilderness",), ("biblical",)),
        VisualVocabularyTerm("olive tree", 3.0, ("olive branch",), ("biblical",)),
        VisualVocabularyTerm("ancient scroll", 3.0, ("scroll", "parchment"), ("biblical",)),
        VisualVocabularyTerm("temple", 3.0, ("holy temple",), ("biblical",)),
        VisualVocabularyTerm("stone path", 3.0, ("stone road",), ("biblical",)),
        VisualVocabularyTerm("dove", 3.0, ("white dove",), ("symbol",)),
        VisualVocabularyTerm("crown of thorns", 3.0, ("thorns",), ("symbol",)),
        VisualVocabularyTerm("empty tomb", 3.0, ("stone tomb",), ("symbol",)),
        VisualVocabularyTerm("water", 3.0, ("still water",), ("nature",)),
        VisualVocabularyTerm("forest light", 3.0, ("sun rays forest",), ("nature",)),
        VisualVocabularyTerm("city skyline", 2.9, ("city at dawn",), ("support",)),
        VisualVocabularyTerm("people walking", 2.9, ("life journey",), ("support",)),
        VisualVocabularyTerm("caregiver", 2.9, ("helping person",), ("community",)),
        VisualVocabularyTerm("hospital visit", 2.9, ("comfort visit",), ("community",)),
        VisualVocabularyTerm("volunteer", 2.9, ("community help",), ("community",)),
        VisualVocabularyTerm("forgive", 2.9, ("reconciliation",), ("theme",)),
        VisualVocabularyTerm("rest", 2.9, ("quiet rest",), ("theme",)),
        VisualVocabularyTerm("waiting", 2.9, ("patient waiting",), ("theme",)),
        VisualVocabularyTerm("journey", 2.9, ("walk of faith",), ("theme",)),
        VisualVocabularyTerm("guidance", 2.9, ("direction",), ("theme",)),
        VisualVocabularyTerm("calling", 2.9, ("purpose",), ("theme",)),
        VisualVocabularyTerm("redemption", 2.9, ("restored life",), ("theme",)),
        VisualVocabularyTerm("testimony", 2.9, ("witness",), ("theme",)),
        VisualVocabularyTerm("deliverance", 2.9, ("libertacao",), ("theme",)),
        VisualVocabularyTerm("breakthrough", 2.9, ("overcoming",), ("theme",)),
    ),
    negative_terms=(
        "christmas tree",
        "santa",
        "santa claus",
        "gift box",
        "shopping",
        "party",
        "nightclub",
        "halloween",
        "easter bunny",
        "fireworks",
        "champagne",
        "disco",
        "sale",
        "fashion show",
    ),
    negative_aliases=(
        "xmas",
        "new year party",
        "holiday shopping",
        "birthday party",
        "dance party",
    ),
)


CHANNEL_VISUAL_VOCABULARIES: dict[str, ChannelVisualVocabulary] = {
    RELIGIOUS_VISUAL_VOCABULARY.key: RELIGIOUS_VISUAL_VOCABULARY,
}


def resolve_channel_visual_vocabulary(channel_key: str | None) -> ChannelVisualVocabulary | None:
    cleaned = str(channel_key or "").strip().lower()
    if not cleaned:
        return None
    return CHANNEL_VISUAL_VOCABULARIES.get(cleaned)
