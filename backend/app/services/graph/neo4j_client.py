"""Neo4j client for the user knowledge graph.

Schema (per-user-scoped):
  Labels:  User, Company, Preference, Goal, Person, Topic, Entity (fallback)
  Edges:   any UPPER_SNAKE relation (WORKS_AT, LIKES, HAS_GOAL, ...)
           with properties { user_id, confidence, created_at, updated_at, count }

Every node carries a `user_id` so multiple users share the same DB without
their graphs colliding. The same entity name under different users is a
distinct node.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from neo4j import AsyncGraphDatabase, AsyncDriver

from app.config import get_settings
from app.models.schemas import GraphEdge, GraphNode, GraphPayload, NodeType, Triple


_TYPE_TO_LABEL: dict[NodeType, str] = {
    "user": "User",
    "company": "Company",
    "preference": "Preference",
    "goal": "Goal",
    "person": "Person",
    "topic": "Topic",
    "other": "Entity",
}

_LABEL_TO_TYPE: dict[str, NodeType] = {v: k for k, v in _TYPE_TO_LABEL.items()}

# Allow only A-Z, 0-9, underscore — relations come from an LLM, so we sanitize
# before string-interpolating into Cypher.
_RELATION_SAFE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _safe_relation(rel: str) -> str:
    rel = (rel or "").upper().replace(" ", "_").replace("-", "_")
    cleaned = "".join(ch for ch in rel if ch in _RELATION_SAFE)
    return cleaned or "RELATED_TO"


def _label_for(t: NodeType) -> str:
    return _TYPE_TO_LABEL.get(t, "Entity")


class Neo4jClient:
    def __init__(self) -> None:
        s = get_settings()
        self._driver: AsyncDriver = AsyncGraphDatabase.driver(
            s.NEO4J_URI, auth=(s.NEO4J_USER, s.NEO4J_PASSWORD)
        )
        self._database = s.NEO4J_DATABASE
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def close(self) -> None:
        await self._driver.close()

    async def init_schema(self) -> None:
        async with self._init_lock:
            if self._initialized:
                return
            stmts = [
                "CREATE CONSTRAINT user_id_name IF NOT EXISTS "
                "FOR (u:User) REQUIRE (u.user_id, u.name) IS UNIQUE",
                "CREATE INDEX entity_user_id IF NOT EXISTS FOR (n:Entity) ON (n.user_id, n.name)",
                "CREATE INDEX company_user_id IF NOT EXISTS FOR (n:Company) ON (n.user_id, n.name)",
                "CREATE INDEX preference_user_id IF NOT EXISTS FOR (n:Preference) ON (n.user_id, n.name)",
                "CREATE INDEX goal_user_id IF NOT EXISTS FOR (n:Goal) ON (n.user_id, n.name)",
                "CREATE INDEX person_user_id IF NOT EXISTS FOR (n:Person) ON (n.user_id, n.name)",
                "CREATE INDEX topic_user_id IF NOT EXISTS FOR (n:Topic) ON (n.user_id, n.name)",
            ]
            async with self._driver.session(database=self._database) as session:
                for s in stmts:
                    try:
                        await session.run(s)
                    except Exception:
                        # Older Neo4j versions or pre-existing constraints — non-fatal.
                        pass
            self._initialized = True

    async def upsert_triple(
        self, user_id: str, triple: Triple, thread_id: Optional[str] = None
    ) -> str:
        rel = _safe_relation(triple.relation)
        s_label = _label_for(triple.subject_type)
        o_label = _label_for(triple.object_type)
        now = datetime.now(timezone.utc).isoformat()

        # Subject is canonicalized to "User" if the LLM said the subject is the user.
        subject_name = triple.subject.strip()
        if triple.subject_type == "user":
            subject_name = "User"

        # Probe first so we can tell the caller whether this was a create
        # vs. an update of an existing edge. We do it in one transaction so
        # the result reflects the state we just wrote.
        # `thread_ids` is a deduped list on the edge — every thread that has
        # asserted this triple. On thread delete, we strip the thread's id;
        # the edge is removed only when the list becomes empty. Same logic
        # applies to nodes via `s.thread_ids` / `o.thread_ids`.
        tids_param = [thread_id] if thread_id else []
        cypher = f"""
        MERGE (s:{s_label} {{ user_id: $user_id, name: $s_name }})
          ON CREATE SET s.created_at = $now,
                        s.thread_ids = $tids
          ON MATCH  SET s.thread_ids = CASE
                         WHEN $tid IS NULL THEN coalesce(s.thread_ids, [])
                         WHEN $tid IN coalesce(s.thread_ids, []) THEN s.thread_ids
                         ELSE coalesce(s.thread_ids, []) + $tid
                       END
        WITH s
        MERGE (o:{o_label} {{ user_id: $user_id, name: $o_name }})
          ON CREATE SET o.created_at = $now,
                        o.thread_ids = $tids
          ON MATCH  SET o.thread_ids = CASE
                         WHEN $tid IS NULL THEN coalesce(o.thread_ids, [])
                         WHEN $tid IN coalesce(o.thread_ids, []) THEN o.thread_ids
                         ELSE coalesce(o.thread_ids, []) + $tid
                       END
        WITH s, o
        OPTIONAL MATCH (s)-[existing:{rel}]->(o)
        WITH s, o, existing IS NOT NULL AS existed,
             existing.valid_from AS old_from, existing.valid_until AS old_until,
             existing.confidence AS old_conf
        MERGE (s)-[r:{rel}]->(o)
          ON CREATE SET r.created_at = $now, r.count = 1, r.confidence = $conf,
                        r.user_id = $user_id,
                        r.valid_from = $valid_from, r.valid_until = $valid_until,
                        r.thread_ids = $tids
          ON MATCH  SET r.updated_at = $now,
                        r.count = coalesce(r.count, 0) + 1,
                        r.confidence = CASE
                          WHEN r.confidence IS NULL THEN $conf
                          ELSE (r.confidence + $conf) / 2.0
                        END,
                        r.valid_from  = coalesce($valid_from,  r.valid_from),
                        r.valid_until = coalesce($valid_until, r.valid_until),
                        r.thread_ids = CASE
                          WHEN $tid IS NULL THEN coalesce(r.thread_ids, [])
                          WHEN $tid IN coalesce(r.thread_ids, []) THEN r.thread_ids
                          ELSE coalesce(r.thread_ids, []) + $tid
                        END
        RETURN existed,
               (existed AND
                 (old_from   <> coalesce($valid_from,  old_from)   OR
                  old_until  <> coalesce($valid_until, old_until)  OR
                  abs(coalesce(old_conf, 0) - $conf) > 0.05)
               ) AS materially_changed
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                cypher,
                user_id=user_id,
                s_name=subject_name,
                o_name=triple.object.strip(),
                conf=float(triple.confidence),
                now=now,
                valid_from=triple.valid_from,
                valid_until=triple.valid_until,
                tid=thread_id,
                tids=tids_param,
            )
            rec = await result.single()
        if not rec or not rec["existed"]:
            return "created"
        return "updated" if rec["materially_changed"] else "reinforced"

    async def delete_triples_for_thread(self, user_id: str, thread_id: str) -> int:
        """Strip a thread_id from every edge/node it created, then drop edges
        and nodes that have no remaining thread references.

        Returns the number of edges removed.
        """
        # Step 1: pull thread_id out of every edge's thread_ids array.
        edge_strip = """
        MATCH (s {user_id: $user_id})-[r]->(o {user_id: $user_id})
        WHERE $tid IN coalesce(r.thread_ids, [])
        SET r.thread_ids = [t IN r.thread_ids WHERE t <> $tid]
        """
        # Step 2: delete edges whose thread_ids array is now empty.
        edge_drop = """
        MATCH (s {user_id: $user_id})-[r]->(o {user_id: $user_id})
        WHERE size(coalesce(r.thread_ids, [])) = 0
        DELETE r
        RETURN count(r) AS removed
        """
        # Step 3: same treatment for nodes (User node is preserved).
        node_strip = """
        MATCH (n {user_id: $user_id})
        WHERE $tid IN coalesce(n.thread_ids, [])
        SET n.thread_ids = [t IN n.thread_ids WHERE t <> $tid]
        """
        node_drop = """
        MATCH (n {user_id: $user_id})
        WHERE coalesce(n.name, '') <> 'User'
          AND size(coalesce(n.thread_ids, [])) = 0
          AND NOT (n)--()
        DELETE n
        """
        async with self._driver.session(database=self._database) as session:
            await session.run(edge_strip, user_id=user_id, tid=thread_id)
            res = await session.run(edge_drop, user_id=user_id)
            rec = await res.single()
            removed = int(rec["removed"]) if rec and rec["removed"] is not None else 0
            await session.run(node_strip, user_id=user_id, tid=thread_id)
            await session.run(node_drop, user_id=user_id)
        return removed

    async def delete_triple(self, user_id: str, triple: Triple) -> bool:
        """Hard-delete the (subject, relation, object) edge for this user.

        Returns True if anything was deleted. Orphan nodes (no remaining edges,
        and not the User node) are then cleaned up so the graph stays tidy.
        """
        rel = _safe_relation(triple.relation)
        subject_name = "User" if triple.subject_type == "user" else triple.subject.strip()
        del_cypher = f"""
        MATCH (s {{user_id: $user_id, name: $s_name}})
              -[r:{rel}]->
              (o {{user_id: $user_id, name: $o_name}})
        DELETE r
        RETURN count(r) AS deleted
        """
        # Two-step: delete the edge, then remove any node that lost its last
        # connection (except the central User node).
        cleanup = """
        MATCH (n {user_id: $user_id})
        WHERE NOT (n)--() AND coalesce(n.name, '') <> 'User'
        DELETE n
        """
        deleted = 0
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                del_cypher,
                user_id=user_id,
                s_name=subject_name,
                o_name=triple.object.strip(),
            )
            rec = await result.single()
            deleted = int(rec["deleted"]) if rec and rec["deleted"] is not None else 0
            if deleted:
                await session.run(cleanup, user_id=user_id)
        return deleted > 0

    async def get_user_graph(self, user_id: str) -> GraphPayload:
        """Return the full graph for a user, formatted for the frontend."""
        cypher = """
        MATCH (s)-[r]->(o)
        WHERE s.user_id = $user_id AND o.user_id = $user_id
        RETURN s.name AS s_name, labels(s) AS s_labels,
               type(r) AS rel, r.confidence AS conf, r.updated_at AS ts,
               r.valid_from AS valid_from, r.valid_until AS valid_until,
               o.name AS o_name, labels(o) AS o_labels
        """
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        async with self._driver.session(database=self._database) as session:
            result = await session.run(cypher, user_id=user_id)
            async for rec in result:
                s_type = _first_known_type(rec["s_labels"])
                o_type = _first_known_type(rec["o_labels"])
                s_id = rec["s_name"]
                o_id = rec["o_name"]
                nodes.setdefault(s_id, GraphNode(id=s_id, type=s_type))
                nodes.setdefault(o_id, GraphNode(id=o_id, type=o_type))
                edges.append(
                    GraphEdge(
                        source=s_id,
                        target=o_id,
                        label=rec["rel"],
                        confidence=float(rec["conf"] or 1.0),
                        timestamp=rec["ts"],
                        valid_from=rec["valid_from"],
                        valid_until=rec["valid_until"],
                    )
                )
        return GraphPayload(nodes=list(nodes.values()), edges=edges)

    async def find_by_entity(
        self, user_id: str, entity: str, limit: int = 20
    ) -> list[dict]:
        """Find facts where a node with this name is subject or object."""
        cypher = """
        MATCH (s {user_id: $user_id, name: $name})-[r]->(o {user_id: $user_id})
        RETURN s.name AS s, type(r) AS rel, o.name AS o, r.confidence AS conf,
               r.valid_from AS valid_from, r.valid_until AS valid_until
        LIMIT $limit
        UNION
        MATCH (s {user_id: $user_id})-[r]->(o {user_id: $user_id, name: $name})
        RETURN s.name AS s, type(r) AS rel, o.name AS o, r.confidence AS conf,
               r.valid_from AS valid_from, r.valid_until AS valid_until
        LIMIT $limit
        """
        out: list[dict] = []
        async with self._driver.session(database=self._database) as session:
            result = await session.run(cypher, user_id=user_id, name=entity, limit=limit)
            async for rec in result:
                out.append(_fact_dict(rec))
        return out

    async def find_by_subject_relation(
        self, user_id: str, subject: str, relation: str, limit: int = 10
    ) -> list[dict]:
        rel = _safe_relation(relation)
        cypher = f"""
        MATCH (s {{user_id: $user_id, name: $name}})-[r:{rel}]->(o {{user_id: $user_id}})
        RETURN s.name AS s, type(r) AS rel, o.name AS o, r.confidence AS conf,
               r.valid_from AS valid_from, r.valid_until AS valid_until
        LIMIT $limit
        """
        out: list[dict] = []
        async with self._driver.session(database=self._database) as session:
            result = await session.run(cypher, user_id=user_id, name=subject, limit=limit)
            async for rec in result:
                out.append(_fact_dict(rec))
        return out

    async def stats(self, user_id: str) -> dict:
        """Counts of nodes / edges / per-label nodes for a user — used by
        the frontend stats badge."""
        cypher_nodes = """
        MATCH (n {user_id: $user_id})
        WITH labels(n) AS lbls
        UNWIND lbls AS lbl
        WITH lbl WHERE lbl <> ''
        RETURN lbl, count(*) AS c
        """
        cypher_edge = """
        MATCH (s {user_id: $user_id})-[r]->(o {user_id: $user_id})
        RETURN count(r) AS edges, count(DISTINCT type(r)) AS rel_types
        """
        cypher_total = """
        MATCH (n {user_id: $user_id})
        RETURN count(n) AS nodes
        """
        per_label: dict[str, int] = {}
        async with self._driver.session(database=self._database) as session:
            r1 = await session.run(cypher_nodes, user_id=user_id)
            async for rec in r1:
                per_label[rec["lbl"]] = int(rec["c"])
            r2 = await session.run(cypher_edge, user_id=user_id)
            er = await r2.single()
            edges = int(er["edges"]) if er else 0
            rel_types = int(er["rel_types"]) if er else 0
            r3 = await session.run(cypher_total, user_id=user_id)
            tr = await r3.single()
            nodes = int(tr["nodes"]) if tr else 0
        return {
            "nodes": nodes,
            "edges": edges,
            "relation_types": rel_types,
            "per_label": per_label,
        }

    async def delete_node_by_name(self, user_id: str, name: str) -> dict:
        """Delete a single node and all of its incident edges for this user.
        Then drop any node that lost its last edge as a result (User node
        is always preserved).

        Returns counts so the caller can surface "freed N edges, M nodes".
        """
        if not name:
            return {"edges": 0, "nodes": 0}
        # First DETACH DELETE the named node — this removes the node and all
        # its incident edges in one shot.
        del_main = """
        MATCH (n {user_id: $user_id, name: $name})
        OPTIONAL MATCH (n)-[r]-()
        WITH n, count(r) AS edges_removed
        DETACH DELETE n
        RETURN edges_removed, 1 AS nodes_removed
        """
        # Then sweep any nodes left orphaned (no edges) — except User.
        sweep = """
        MATCH (n {user_id: $user_id})
        WHERE NOT (n)--() AND coalesce(n.name, '') <> 'User'
        WITH collect(n) AS orphans
        FOREACH (o IN orphans | DELETE o)
        RETURN size(orphans) AS swept
        """
        async with self._driver.session(database=self._database) as session:
            r1 = await session.run(del_main, user_id=user_id, name=name)
            rec = await r1.single()
            edges = int(rec["edges_removed"]) if rec else 0
            nodes = int(rec["nodes_removed"]) if rec else 0
            r2 = await session.run(sweep, user_id=user_id)
            sw = await r2.single()
            nodes += int(sw["swept"]) if sw else 0
        return {"edges": edges, "nodes": nodes}

    async def delete_user(self, user_id: str) -> int:
        """Drop every node + edge belonging to this user."""
        cypher = """
        MATCH (n {user_id: $user_id})
        DETACH DELETE n
        RETURN count(n) AS removed
        """
        async with self._driver.session(database=self._database) as session:
            res = await session.run(cypher, user_id=user_id)
            rec = await res.single()
            return int(rec["removed"]) if rec and rec["removed"] is not None else 0

    async def long_term_profile(self, user_id: str, limit: int = 40) -> list[dict]:
        """Return the user's most-confident, most-reinforced facts — used as
        the long-term memory snapshot for the response prompt.

        Returns BOTH 1-hop facts (User -> X) AND 2-hop facts (X -> Y, where X
        is a direct neighbor of User). Without the 2-hop expansion the bot
        sees `User HAS_BROTHER Arjun` but not `Arjun WORKS_IN Pune`, so it
        can't answer "where does my brother work?" even though the data is in
        the graph.
        """
        cypher = """
        // Hop 1: every direct edge from User.
        MATCH (u:User {user_id: $user_id, name: 'User'})-[r1]->(n1)
        WHERE n1.user_id = $user_id
        RETURN u.name AS s, type(r1) AS rel, n1.name AS o,
               coalesce(r1.confidence, 0.5) AS conf,
               coalesce(r1.count, 1) AS count,
               r1.valid_from AS valid_from, r1.valid_until AS valid_until,
               1 AS hop
        UNION
        // Hop 2: every outgoing edge from one of User's direct neighbors.
        // Excludes edges that point back to User (already covered by hop 1).
        MATCH (u:User {user_id: $user_id, name: 'User'})-[]-(n1)-[r2]->(n2)
        WHERE n1.user_id = $user_id AND n2.user_id = $user_id
          AND n2 <> u AND n1 <> u
        RETURN n1.name AS s, type(r2) AS rel, n2.name AS o,
               coalesce(r2.confidence, 0.5) AS conf,
               coalesce(r2.count, 1) AS count,
               r2.valid_from AS valid_from, r2.valid_until AS valid_until,
               2 AS hop
        """
        out: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        async with self._driver.session(database=self._database) as session:
            result = await session.run(cypher, user_id=user_id)
            async for rec in result:
                key = (rec["s"], rec["rel"], rec["o"])
                if key in seen:
                    continue
                seen.add(key)
                out.append(_fact_dict(rec))
        # Sort: User-as-subject first, then by confidence descending, so the
        # User's own attributes lead the bounded list. 2-hop facts about
        # related entities come after but still inside the cap.
        out.sort(
            key=lambda f: (
                0 if f.get("s") == "User" else 1,
                -float(f.get("conf") or 0.5),
            )
        )
        return out[:limit]


def _first_known_type(labels: list[str]) -> NodeType:
    for lbl in labels:
        if lbl in _LABEL_TO_TYPE:
            return _LABEL_TO_TYPE[lbl]
    return "other"


def _fact_dict(rec) -> dict:
    """Common projection for a Cypher record returning a (s, rel, o) fact
    plus optional date validity. Keys not in the record are returned as None."""
    keys = set(rec.keys())
    return {
        "s": rec["s"],
        "rel": rec["rel"],
        "o": rec["o"],
        "conf": rec["conf"],
        "valid_from": rec["valid_from"] if "valid_from" in keys else None,
        "valid_until": rec["valid_until"] if "valid_until" in keys else None,
    }


_singleton: Optional[Neo4jClient] = None


def get_graph_client() -> Neo4jClient:
    global _singleton
    if _singleton is None:
        _singleton = Neo4jClient()
    return _singleton


async def reset_graph_client() -> None:
    """Tear down the cached Neo4j driver so the next call picks up freshly
    saved URI / credentials from runtime config."""
    global _singleton
    if _singleton is not None:
        try:
            await _singleton.close()
        except Exception:
            pass
        _singleton = None
