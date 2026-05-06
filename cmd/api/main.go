package main

import (
	"bytes"
	"encoding/json"
	"context"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/gofiber/fiber/v2"
)

type recommendRequest struct {
	Query string `json:"query"`
	K     int    `json:"k"`
}

type embedRequest struct {
	Text string `json:"text"`
}

type embedResponse struct {
	Vector []float64 `json:"vector"`
}

type weaviateGraphQLResponse struct {
	Data struct {
		Get map[string][]struct {
			ImdbID       string   `json:"imdbId"`
			Type         string   `json:"type"`
			PrimaryTitle string   `json:"primaryTitle"`
			OriginalTitle string  `json:"originalTitle"`
			StartYear    int      `json:"startYear"`
			EndYear      int      `json:"endYear"`
			Genres       []string `json:"genres"`
			Plot         string   `json:"plot"`
			Rating       float64  `json:"rating"`
			Votes        int      `json:"votes"`
			ImageURL     string   `json:"imageUrl"`
			Additional   struct {
				Distance float64 `json:"distance"`
			} `json:"_additional"`
		} `json:"Get"`
	} `json:"data"`
	Errors []map[string]interface{} `json:"errors"`
}

type recommendItem struct {
	ImdbID       string   `json:"imdb_id"`
	Type         string   `json:"type"`
	PrimaryTitle string   `json:"primary_title"`
	OriginalTitle string  `json:"original_title"`
	StartYear    int      `json:"start_year"`
	EndYear      int      `json:"end_year"`
	Genres       []string `json:"genres"`
	Plot         string   `json:"plot"`
	Rating       float64  `json:"rating"`
	Votes        int      `json:"votes"`
	ImageURL     string   `json:"image_url"`
	Distance     float64  `json:"distance"`
	Score        float64  `json:"score"`
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func weaviateReady(ctx context.Context) error {
	scheme := env("WEAVIATE_SCHEME", "http")
	host := env("WEAVIATE_HOST", "localhost")
	port := env("WEAVIATE_PORT", "8080")

	url := fmt.Sprintf("%s://%s:%s/v1/.well-known/ready", scheme, host, port)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}

	client := &http.Client{Timeout: 2 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("weaviate not ready: status=%d", resp.StatusCode)
	}
	return nil
}

func embedQuery(ctx context.Context, query string) ([]float64, error) {
	embedderURL := env("EMBEDDER_URL", "http://localhost:8000/embed")
	body, _ := json.Marshal(embedRequest{Text: query})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, embedderURL, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("embedder failed: status=%d", resp.StatusCode)
	}

	var out embedResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, err
	}
	if len(out.Vector) == 0 {
		return nil, fmt.Errorf("embedder returned empty vector")
	}
	return out.Vector, nil
}

func queryWeaviate(ctx context.Context, vector []float64, k int) ([]recommendItem, error) {
	scheme := env("WEAVIATE_SCHEME", "http")
	host := env("WEAVIATE_HOST", "localhost")
	port := env("WEAVIATE_PORT", "8080")
	className := env("WEAVIATE_CLASS_NAME", "KDrama")
	url := fmt.Sprintf("%s://%s:%s/v1/graphql", scheme, host, port)

	var sb strings.Builder
	for i, v := range vector {
		if i > 0 {
			sb.WriteString(",")
		}
		sb.WriteString(fmt.Sprintf("%.8f", v))
	}

	query := fmt.Sprintf(`{
		Get {
			%s(
				nearVector: { vector: [%s] }
				limit: %d
			) {
				imdbId
				type
				primaryTitle
				originalTitle
				startYear
				endYear
				genres
				plot
				rating
				votes
				imageUrl
				_additional {
					distance
				}
			}
		}
	}`, className, sb.String(), k)

	payload, _ := json.Marshal(fiber.Map{"query": query})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("weaviate graphql failed: status=%d", resp.StatusCode)
	}

	var out weaviateGraphQLResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, err
	}
	if len(out.Errors) > 0 {
		return nil, fmt.Errorf("weaviate graphql errors: %v", out.Errors)
	}

	rows := out.Data.Get[className]
	items := make([]recommendItem, 0, len(rows))
	for _, r := range rows {
		score := 1.0 - (r.Additional.Distance / 2.0) // cosine distance (0..2) -> similarity-ish score (1..0)
		items = append(items, recommendItem{
			ImdbID:       r.ImdbID,
			Type:         r.Type,
			PrimaryTitle: r.PrimaryTitle,
			OriginalTitle: r.OriginalTitle,
			StartYear:    r.StartYear,
			EndYear:      r.EndYear,
			Genres:       r.Genres,
			Plot:         r.Plot,
			Rating:       r.Rating,
			Votes:        r.Votes,
			ImageURL:     r.ImageURL,
			Distance:     r.Additional.Distance,
			Score:        score,
		})
	}
	return items, nil
}

func main() {
	addr := env("HTTP_ADDR", ":8080")

	app := fiber.New(fiber.Config{
		AppName: "kdrama-api",
	})

	// Minimal web demo (static assets)
	app.Static("/", "./web/static", fiber.Static{
		Index: "index.html",
	})

	// Health endpoints for Compose / basic ops
	app.Get("/healthz", func(c *fiber.Ctx) error {
		return c.JSON(fiber.Map{"status": "ok"})
	})

	app.Get("/readyz", func(c *fiber.Ctx) error {
		ctx, cancel := context.WithTimeout(c.Context(), 2*time.Second)
		defer cancel()

		if err := weaviateReady(ctx); err != nil {
			return c.Status(fiber.StatusServiceUnavailable).JSON(fiber.Map{
				"status": "not_ready",
				"error":  err.Error(),
			})
		}

		return c.JSON(fiber.Map{"status": "ready"})
	})

	app.Post("/api/recommend", func(c *fiber.Ctx) error {
		var in recommendRequest
		if err := c.BodyParser(&in); err != nil {
			return c.Status(fiber.StatusBadRequest).JSON(fiber.Map{"error": "invalid json body"})
		}
		in.Query = strings.TrimSpace(in.Query)
		if in.Query == "" {
			return c.Status(fiber.StatusBadRequest).JSON(fiber.Map{"error": "query is required"})
		}
		if in.K <= 0 || in.K > 20 {
			in.K = 5
		}

		ctx, cancel := context.WithTimeout(c.Context(), 45*time.Second)
		defer cancel()

		vector, err := embedQuery(ctx, in.Query)
		if err != nil {
			return c.Status(fiber.StatusBadGateway).JSON(fiber.Map{"error": err.Error()})
		}
		items, err := queryWeaviate(ctx, vector, in.K)
		if err != nil {
			return c.Status(fiber.StatusBadGateway).JSON(fiber.Map{"error": err.Error()})
		}

		return c.JSON(fiber.Map{
			"query": in.Query,
			"k":     in.K,
			"items": items,
		})
	})

	log.Printf("listening on %s", addr)
	if err := app.Listen(addr); err != nil {
		log.Fatal(err)
	}
}
