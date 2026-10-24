import type { NewsArticle } from "@/lib/api/types";
import { fmtNewsTime } from "@/lib/format";

const images = ["/assets/vpw-editorial-architecture.png", "/assets/vpw-editorial-tyre.png", "/assets/vpw-editorial-line.png"];

// The stock artwork is layered underneath, so it still shows when a feed image is missing or fails to load.
const storyImage = (article: NewsArticle, index: number) => {
  const fallback = `url(${images[index % images.length]})`;
  return { backgroundImage: article.image_url ? `url(${JSON.stringify(article.image_url)}), ${fallback}` : fallback };
};

export function EditorialNews({ articles, compact = false }: { articles: NewsArticle[]; compact?: boolean }) {
  if (!articles.length) return null;
  if (compact) return <div className="weekend-news-row">{articles.slice(0, 4).map((article, index) => <a href={article.url} target="_blank" rel="noreferrer" key={article.url} className="weekend-news-item"><span className="story-image" style={storyImage(article, index)} aria-hidden="true" /><span><small>{fmtNewsTime(article.published_at)}</small><strong>{article.headline}</strong><em>{article.source}</em></span></a>)}</div>;
  const [lead, ...rest] = articles;
  return <div className="editorial-news">
    <a className="lead-story" href={lead.url} target="_blank" rel="noreferrer"><span className="lead-image story-image" style={storyImage(lead, 0)} aria-hidden="true" /><span className="story-meta">{lead.source} · {fmtNewsTime(lead.published_at)}</span><h3>{lead.headline}</h3>{lead.summary ? <p>{lead.summary}</p> : null}</a>
    <div className="secondary-stories">{rest.slice(0, 4).map((article, index) => <a href={article.url} target="_blank" rel="noreferrer" key={article.url}><span className="story-image" style={storyImage(article, index + 1)} aria-hidden="true" /><span><strong>{article.headline}</strong><small>{article.source} · {fmtNewsTime(article.published_at)}</small></span></a>)}</div>
  </div>;
}
