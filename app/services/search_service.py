"""Search Service Engine for PostgreSQL Native Full-Text Search and Trigram Similarity.

Architectural Capabilities:
1. Native PostgreSQL Full-Text Search using TSVector, TSQuery, and GIN Inverted Indexes.
2. Relevance ranking via ts_rank with weight differential (Title: Weight A, Description: Weight B).
3. Fuzzy Typo-Tolerant search powered by Trigram Similarity (pg_trgm).
4. Hybrid Search Fallback: Automatic degradation to fuzzy trigram search when exact TSVector yields 0 matches.
5. Cross-dialect test compatibility: Mathematical trigram Jaccard similarity and Porter stemming emulation on SQLite.
"""

from __future__ import annotations

import re
from typing import cast

from sqlalchemy import Table, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base
from app.models.catalog_search import SearchableProductModel
from app.schemas.search import (
    CreateProductRequest,
    SearchResponse,
    SearchResultItem,
)


def _stem_word(word: str) -> str:
    """Deterministic lightweight Porter stemming rules for English terms.

    Normalizes inflected forms (e.g. running -> run, shoes -> shoe, phones -> phone).
    """
    w = word.lower().strip()
    if len(w) <= 3:
        return w

    # Step 1: Plurals / s-endings
    if w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("es") and len(w) > 3 and w[-3] not in "aeiou":
        w = w[:-2]
    elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        w = w[:-1]

    # Step 2: Gerund / Participle -ing
    if w.endswith("ing") and len(w) > 4:
        stem = w[:-3]
        # Double consonant reduction (e.g. running -> run, stepping -> step)
        if len(stem) >= 3 and stem[-1] == stem[-2] and stem[-1] not in "lsz":
            stem = stem[:-1]
        w = stem
    elif w.endswith("ed") and len(w) > 4:
        stem = w[:-2]
        if len(stem) >= 3 and stem[-1] == stem[-2] and stem[-1] not in "lsz":
            stem = stem[:-1]
        w = stem

    return w


def _extract_trigrams(text_val: str) -> set[str]:
    """Extract character 3-grams following PostgreSQL pg_trgm specification.

    PostgreSQL pg_trgm wraps text with two leading spaces and one trailing space:
    "  " + text.lower() + " "
    """
    normalized = "  " + text_val.lower() + " "
    if len(normalized) < 3:
        return set()
    return {normalized[i : i + 3] for i in range(len(normalized) - 2)}


def _calculate_trigram_similarity(str1: str, str2: str) -> float:
    """Calculate PostgreSQL pg_trgm Jaccard Similarity coefficient.

    Similarity = |Trigram(s1) ∩ Trigram(s2)| / |Trigram(s1) ∪ Trigram(s2)|
    Range: [0.0, 1.0]
    """
    t1 = _extract_trigrams(str1)
    t2 = _extract_trigrams(str2)
    if not t1 or not t2:
        return 0.0

    intersection = len(t1.intersection(t2))
    union = len(t1.union(t2))
    if union == 0:
        return 0.0
    return round(intersection / union, 4)


def _calculate_composite_similarity(query: str, target: str) -> float:
    """Calculate composite trigram similarity supporting both whole-string and word-level matches."""
    whole_sim = _calculate_trigram_similarity(query, target)
    q_words = [w for w in re.findall(r"\w+", query.lower()) if len(w) >= 2]
    t_words = [w for w in re.findall(r"\w+", target.lower()) if len(w) >= 2]
    if not q_words or not t_words:
        return whole_sim

    word_scores: list[float] = []
    for qw in q_words:
        best_w_sim = max((_calculate_trigram_similarity(qw, tw) for tw in t_words), default=0.0)
        word_scores.append(best_w_sim)

    avg_word_sim = sum(word_scores) / len(word_scores)
    return round(max(whole_sim, avg_word_sim), 4)


class SearchService:
    """Enterprise Search Engine orchestrating Full-Text Search, Trigrams, and Hybrid Fallbacks."""

    @classmethod
    async def ensure_table_exists(cls, session: AsyncSession) -> None:
        """Ensure searchable_products table and indexes exist in active database."""
        bind = session.bind
        dialect_name = bind.dialect.name if bind else "sqlite"
        if dialect_name == "sqlite":
            conn = await session.connection()
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(
                    sync_conn, tables=[cast(Table, SearchableProductModel.__table__)]
                )
            )

    @classmethod
    async def create_product(
        cls,
        session: AsyncSession,
        request: CreateProductRequest,
    ) -> SearchableProductModel:
        """Persist a new searchable product entity into the database."""
        await cls.ensure_table_exists(session)
        product = SearchableProductModel(
            title=request.title.strip(),
            description=request.description.strip(),
            brand=request.brand.strip(),
            price=request.price,
        )
        session.add(product)
        await session.flush()

        bind = session.bind
        dialect_name = bind.dialect.name if bind else "sqlite"

        if dialect_name == "postgresql":
            # Populate PostgreSQL TSVector with weights (Title: Weight A, Description: Weight B)
            await session.execute(
                text(
                    """
                    UPDATE searchable_products
                    SET search_vector = (
                        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
                        setweight(to_tsvector('english', coalesce(description, '')), 'B')
                    )
                    WHERE id = :id;
                    """
                ),
                {"id": product.id},
            )
        else:
            # SQLite fallback: store concatenated normalized text in search_vector
            product.search_vector = f"{product.title} {product.description}"

        await session.commit()
        await session.refresh(product)
        return product

    @classmethod
    async def search_products_full_text(
        cls,
        session: AsyncSession,
        query_str: str,
        limit: int = 20,
    ) -> list[SearchResultItem]:
        """Perform stemmed full-text search matching lexemes with relevance ranking.

        In PostgreSQL: uses websearch_to_tsquery('english', query_str) and ts_rank.
        In SQLite: emulates Porter stemming and weighted relevance (Title: 1.0, Description: 0.4).
        """
        await cls.ensure_table_exists(session)
        clean_query = query_str.strip()
        if not clean_query:
            return []

        bind = session.bind
        dialect_name = bind.dialect.name if bind else "sqlite"

        if dialect_name == "postgresql":
            # PostgreSQL native TSVector query with ts_rank
            stmt = (
                select(
                    SearchableProductModel,
                    func.ts_rank(
                        SearchableProductModel.search_vector,
                        func.websearch_to_tsquery("english", clean_query),
                    ).label("rank_score"),
                )
                .where(
                    SearchableProductModel.search_vector.op("@@")(
                        func.websearch_to_tsquery("english", clean_query)
                    )
                )
                .order_by(text("rank_score DESC"), SearchableProductModel.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.all()

            return [
                SearchResultItem(
                    id=row[0].id,
                    title=row[0].title,
                    description=row[0].description,
                    brand=row[0].brand,
                    price=row[0].price,
                    score=round(float(row[1]), 4),
                    match_type="FULL_TEXT",
                )
                for row in rows
            ]
        else:
            # SQLite / Test runner emulation: Tokenization, Porter stemming, and weighted scoring
            raw_tokens = re.findall(r"\w+", clean_query.lower())
            if not raw_tokens:
                return []
            query_stems = [_stem_word(t) for t in raw_tokens]

            stmt_all = select(SearchableProductModel)
            res_all = await session.execute(stmt_all)
            all_products = res_all.scalars().all()

            scored_items: list[tuple[SearchableProductModel, float]] = []

            for prod in all_products:
                title_tokens = [_stem_word(t) for t in re.findall(r"\w+", prod.title.lower())]
                desc_tokens = [_stem_word(t) for t in re.findall(r"\w+", prod.description.lower())]

                matched_stems = 0
                score = 0.0

                for stem in query_stems:
                    title_match = stem in title_tokens
                    desc_match = stem in desc_tokens

                    if title_match or desc_match:
                        matched_stems += 1
                        # Weight A (Title) = 1.0, Weight B (Description) = 0.4
                        if title_match:
                            score += 1.0
                        if desc_match:
                            score += 0.4

                # Require all search terms to be matched (AND semantics like websearch_to_tsquery)
                if matched_stems == len(query_stems):
                    norm_score = min(1.0, round(score / (len(query_stems) * 1.4), 4))
                    scored_items.append((prod, norm_score))

            # Sort by relevance score DESC, then title ASC
            scored_items.sort(key=lambda x: (x[1], x[0].price), reverse=True)
            matched_slice = scored_items[:limit]

            return [
                SearchResultItem(
                    id=prod.id,
                    title=prod.title,
                    description=prod.description,
                    brand=prod.brand,
                    price=prod.price,
                    score=score,
                    match_type="FULL_TEXT",
                )
                for prod, score in matched_slice
            ]

    @classmethod
    async def search_products_fuzzy_trigram(
        cls,
        session: AsyncSession,
        typo_query: str,
        similarity_threshold: float = 0.3,
        limit: int = 20,
    ) -> list[SearchResultItem]:
        """Perform fuzzy typo-tolerant search using Trigram Similarity (pg_trgm).

        Catches severe spelling mistakes and transpositions (e.g. 'aple iphon' -> 'Apple iPhone').
        """
        await cls.ensure_table_exists(session)
        clean_query = typo_query.strip()
        if not clean_query:
            return []

        bind = session.bind
        dialect_name = bind.dialect.name if bind else "sqlite"

        if dialect_name == "postgresql":
            # PostgreSQL native pg_trgm similarity
            sim_expr = func.similarity(SearchableProductModel.title, clean_query).label("sim_score")
            stmt = (
                select(SearchableProductModel, sim_expr)
                .where(func.similarity(SearchableProductModel.title, clean_query) >= similarity_threshold)
                .order_by(text("sim_score DESC"), SearchableProductModel.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.all()

            return [
                SearchResultItem(
                    id=row[0].id,
                    title=row[0].title,
                    description=row[0].description,
                    brand=row[0].brand,
                    price=row[0].price,
                    score=round(float(row[1]), 4),
                    match_type="FUZZY_TRIGRAM",
                )
                for row in rows
            ]
        else:
            # SQLite test runner emulation: Mathematical pg_trgm character 3-gram calculation
            stmt_all = select(SearchableProductModel)
            res_all = await session.execute(stmt_all)
            all_products = res_all.scalars().all()

            fuzzy_matches: list[tuple[SearchableProductModel, float]] = []

            for prod in all_products:
                title_sim = _calculate_composite_similarity(clean_query, prod.title)
                brand_sim = _calculate_composite_similarity(clean_query, prod.brand)
                combined_sim = _calculate_composite_similarity(clean_query, f"{prod.brand} {prod.title}")
                best_sim = max(title_sim, brand_sim, combined_sim)

                if best_sim >= similarity_threshold:
                    fuzzy_matches.append((prod, best_sim))

            fuzzy_matches.sort(key=lambda x: x[1], reverse=True)
            matched_slice = fuzzy_matches[:limit]

            return [
                SearchResultItem(
                    id=prod.id,
                    title=prod.title,
                    description=prod.description,
                    brand=prod.brand,
                    price=prod.price,
                    score=sim,
                    match_type="FUZZY_TRIGRAM",
                )
                for prod, sim in matched_slice
            ]

    @classmethod
    async def search_products_hybrid(
        cls,
        session: AsyncSession,
        query_str: str,
        fuzzy: bool = False,
        limit: int = 20,
        similarity_threshold: float = 0.3,
    ) -> SearchResponse:
        """Execute hybrid search with automatic degradation from TSVector to Trigram.

        1. If fuzzy=True: executes Trigram search immediately.
        2. If fuzzy=False: attempts exact stemmed TSVector search.
        3. If TSVector yields zero results: automatically degrades to Trigram Fuzzy search!
        """
        clean_query = query_str.strip()
        if not clean_query:
            return SearchResponse(query=query_str, strategy="EMPTY", total=0, results=[])

        if fuzzy:
            results = await cls.search_products_fuzzy_trigram(
                session=session,
                typo_query=clean_query,
                similarity_threshold=similarity_threshold,
                limit=limit,
            )
            return SearchResponse(
                query=clean_query,
                strategy="FUZZY_TRIGRAM",
                total=len(results),
                results=results,
            )

        # 1. Attempt High-Precision Full-Text Search (TSVector)
        ft_results = await cls.search_products_full_text(
            session=session,
            query_str=clean_query,
            limit=limit,
        )
        if ft_results:
            return SearchResponse(
                query=clean_query,
                strategy="FULL_TEXT",
                total=len(ft_results),
                results=ft_results,
            )

        # 2. Automatic Hybrid Degradation: Fallback to High-Recall Fuzzy Trigram Search
        fuzzy_results = await cls.search_products_fuzzy_trigram(
            session=session,
            typo_query=clean_query,
            similarity_threshold=similarity_threshold,
            limit=limit,
        )
        strategy = "FUZZY_TRIGRAM" if fuzzy_results else "EMPTY"
        return SearchResponse(
            query=clean_query,
            strategy=strategy,
            total=len(fuzzy_results),
            results=fuzzy_results,
        )

    @classmethod
    async def suggest_autocomplete(
        cls,
        session: AsyncSession,
        prefix: str,
        limit: int = 5,
    ) -> list[str]:
        """Provide sub-millisecond query suggestions powered by prefix and trigram matching."""
        await cls.ensure_table_exists(session)
        clean_prefix = prefix.strip().lower()
        if not clean_prefix:
            return []

        stmt = select(SearchableProductModel.title, SearchableProductModel.brand).distinct()
        res = await session.execute(stmt)
        candidates: set[str] = set()

        for title, brand in res.all():
            candidates.add(title)
            candidates.add(brand)

        scored_candidates: list[tuple[str, float]] = []

        for candidate in candidates:
            cand_lower = candidate.lower()
            if cand_lower.startswith(clean_prefix):
                # Highest priority: exact prefix match
                scored_candidates.append((candidate, 1.0))
            elif clean_prefix in cand_lower:
                # Secondary priority: substring match
                scored_candidates.append((candidate, 0.7))
            else:
                # Tertiary priority: trigram similarity
                sim = _calculate_trigram_similarity(clean_prefix, candidate)
                if sim >= 0.2:
                    scored_candidates.append((candidate, sim))

        scored_candidates.sort(key=lambda x: (x[1], -len(x[0])), reverse=True)
        return [item[0] for item in scored_candidates[:limit]]
