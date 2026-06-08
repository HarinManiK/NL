import ReactMarkdown from "react-markdown";

type PageProps = {
  params: { id: string };
};

type PublicRun = {
  run_id: string;
  article: string;
  newsletter_subject: string;
  thumbnail_url: string | null;
  created_at: string;
};

// Replace with your production backend URL via env variable
const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.01:8000";

async function getPublicRun(id: string): Promise<PublicRun | null> {
  const res = await fetch(`${API_BASE}/public/runs/${id}`, { next: { revalidate: 60 } });
  if (!res.ok) return null;
  return res.json();
}

export async function generateMetadata({ params }: PageProps) {
  const run = await getPublicRun(params.id);
  
  if (!run) {
    return { title: "Article Not Found" };
  }

  const title = run.newsletter_subject || "Tech Briefing & Executive Summary";
  const description = "Read the full technical deep dive and analysis.";

  return {
    title,
    description,
    openGraph: {
      title,
      description,
      type: "article",
      images: run.thumbnail_url ? [{ url: run.thumbnail_url, width: 1024, height: 1024 }] : [],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: run.thumbnail_url ? [run.thumbnail_url] : [],
    },
  };
}

export default async function PublicArticlePage({ params }: PageProps) {
  const run = await getPublicRun(params.id);

  if (!run) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#0a0a0a] text-white">
        <p className="text-xl text-neutral-400">Article not found.</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white font-sans selection:bg-neutral-800 selection:text-white">
      <main className="max-w-3xl mx-auto px-6 py-12">
        
        {/* Header / Subject */}
        <header className="mb-10 text-center">
          <h1 className="text-4xl md:text-5xl font-bold tracking-tight text-neutral-100 mb-4 leading-tight">
            {run.newsletter_subject || "Tech Briefing & Executive Summary"}
          </h1>
          <time className="text-neutral-500 font-mono text-sm tracking-widest uppercase">
            {new Date(run.created_at).toLocaleDateString("en-US", { 
              weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' 
            })}
          </time>
        </header>

        {/* Thumbnail Image */}
        {run.thumbnail_url && (
          <div className="mb-12 rounded-xl overflow-hidden shadow-2xl border border-neutral-800/50">
            <img 
              src={run.thumbnail_url} 
              alt="Article Thumbnail" 
              className="w-full h-auto object-cover max-h-[500px]"
            />
          </div>
        )}

        {/* Article Body Content */}
        <article className="prose prose-invert prose-neutral max-w-none prose-headings:font-bold prose-headings:tracking-tight prose-a:text-blue-400 hover:prose-a:text-blue-300 prose-img:rounded-xl">
          <ReactMarkdown>
            {run.article || "No content generated."}
          </ReactMarkdown>
        </article>

      </main>
      
      {/* Footer */}
      <footer className="max-w-3xl mx-auto px-6 py-8 mt-12 border-t border-neutral-800 text-center text-sm text-neutral-500">
        <p>Automatically generated via AI Newsletter Digest.</p>
      </footer>
    </div>
  );
}
