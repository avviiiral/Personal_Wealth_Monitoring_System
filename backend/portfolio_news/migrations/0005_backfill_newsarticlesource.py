from django.db import migrations


def backfill_sources(apps, schema_editor):
    """
    Every NewsArticle created before NewsArticleSource existed
    already has a `source`/`url` on it (the first, and only,
    publisher seen at the time). Snapshot that as its first
    NewsArticleSource row so existing history isn't lost, and
    set source_quality/source_count to match.
    """

    NewsArticle = apps.get_model("portfolio_news", "NewsArticle")
    NewsArticleSource = apps.get_model(
        "portfolio_news", "NewsArticleSource"
    )

    # Import at runtime (not from the historical model state) -
    # these are plain functions with no model dependency, so
    # using the real, current implementations is fine and
    # avoids duplicating the classification lists here.
    from portfolio_news.services.deduplication import compute_url_hash
    from portfolio_news.services.source_quality import classify_source

    articles = NewsArticle.objects.exclude(url="").iterator()

    for article in articles:
        if article.sources.exists():
            continue

        tier = classify_source(article.source)

        NewsArticleSource.objects.get_or_create(
            article=article,
            url_hash=compute_url_hash(article.url),
            defaults={
                "publisher_name": article.source,
                "url": article.url,
                "quality_tier": tier,
                "published_at": article.published_at,
            },
        )

        article.source_quality = tier
        article.source_count = 1
        article.save(update_fields=["source_quality", "source_count"])


def noop_reverse(apps, schema_editor):
    # Intentionally irreversible in the sense of restoring prior
    # state exactly, but we leave the rows in place rather than
    # deleting on reverse - safer for a backfill migration.
    pass


class Migration(migrations.Migration):

    dependencies = [
        (
            "portfolio_news",
            "0004_newsarticle_source_count_newsarticle_source_quality_and_more",
        ),
    ]

    operations = [
        migrations.RunPython(backfill_sources, noop_reverse),
    ]
