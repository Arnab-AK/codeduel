import { useEffect, useRef, useState } from "react";
import { api, matchSocketUrl } from "../api.js";

export default function MatchView({ session, matchId, onExit }) {
  const [problem, setProblem] = useState(null);
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [myResult, setMyResult] = useState(null);
  const [opponentProgress, setOpponentProgress] = useState(null);
  const [matchComplete, setMatchComplete] = useState(null);
  const [error, setError] = useState(null);
  const wsRef = useRef(null);

  // Load the match's assigned problem once, on entering this screen.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const match = await api.getMatch(matchId);
      if (cancelled) return;
      const p = await api.getProblem(match.problem_id);
      if (cancelled) return;
      setProblem(p);
      if (match.status === "completed") {
        setMatchComplete({ winnerId: match.winner_id });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [matchId]);

  // One WebSocket for the life of this screen. See api.js/matchSocketUrl
  // for why auth here is a query param, not a header.
  useEffect(() => {
    const ws = new WebSocket(matchSocketUrl(matchId, session.token));
    wsRef.current = ws;

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);

      if (msg.type === "snapshot") {
        const opponentEntry = Object.entries(msg.players).find(
          ([playerId]) => playerId !== session.user_id
        );
        if (opponentEntry && opponentEntry[1]) setOpponentProgress(opponentEntry[1]);
        if (msg.match_status === "completed") {
          setMatchComplete({ winnerId: msg.winner_id });
        }
      } else if (msg.type === "progress") {
        // Only the OPPONENT's progress arrives here for us to act on --
        // our own submission's result already came back synchronously
        // from the POST /submissions response (see handleSubmit).
        if (msg.player_id !== session.user_id) {
          setOpponentProgress({
            passed_count: msg.passed_count,
            total_count: msg.total_count,
            status: msg.status,
          });
        }
      } else if (msg.type === "match_complete") {
        const won = msg.winner_id === session.user_id;
        setMatchComplete({
          winnerId: msg.winner_id,
          myRatingBefore: won ? msg.winner_rating_before : msg.loser_rating_before,
          myRatingAfter: won ? msg.winner_rating_after : msg.loser_rating_after,
        });
      }
    };

    return () => ws.close();
  }, [matchId, session.token, session.user_id]);

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);
    try {
      const result = await api.submit(session.token, {
        problemId: problem.id,
        code,
        matchId,
      });
      setMyResult(result);
      if (result.won_match) {
        setMatchComplete({ winnerId: session.user_id, myRatingAfter: result.new_rating });
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (!problem) {
    return (
      <div className="centered">
        <p>Loading match…</p>
      </div>
    );
  }

  const iWon = matchComplete && matchComplete.winnerId === session.user_id;

  return (
    <div className="match-layout">
      <div className="panel problem-panel">
        <h2>
          {problem.title} <span className={`badge badge-${problem.difficulty}`}>{problem.difficulty}</span>
        </h2>
        <p className="description">{problem.description}</p>
        {problem.examples.map((example, i) => (
          <div key={i} className="example">
            <div>
              <strong>Input:</strong> <code>{JSON.stringify(example.input)}</code>
            </div>
            <div>
              <strong>Output:</strong> <code>{example.expected_output}</code>
            </div>
          </div>
        ))}

        <div className="opponent-progress">
          <h3>Opponent</h3>
          {opponentProgress ? (
            <p>
              {opponentProgress.passed_count}/{opponentProgress.total_count} tests passing
              <span className={`status status-${opponentProgress.status}`}>
                {" "}
                ({opponentProgress.status})
              </span>
            </p>
          ) : (
            <p className="muted">No submissions yet</p>
          )}
        </div>
      </div>

      <div className="panel code-panel">
        <textarea
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder="Read from stdin, write to stdout..."
          disabled={!!matchComplete}
          spellCheck={false}
        />
        <button onClick={handleSubmit} disabled={submitting || !code || !!matchComplete}>
          {submitting ? "Running in sandbox…" : "Submit"}
        </button>
        {error && <div className="error">{error}</div>}

        {myResult && !matchComplete && (
          <div className={`result result-${myResult.status}`}>
            {myResult.status} — {myResult.passed_count}/{myResult.total_count} tests passing
          </div>
        )}

        {matchComplete && (
          <div className={`banner ${iWon ? "banner-win" : "banner-lose"}`}>
            <h2>{iWon ? "You won!" : "Opponent won"}</h2>
            {matchComplete.myRatingAfter != null && <p>New rating: {matchComplete.myRatingAfter}</p>}
            <button onClick={onExit}>Back to lobby</button>
          </div>
        )}
      </div>
    </div>
  );
}
