export function MainContent({ children }: { children: React.ReactNode }) {
  return (
    <main className="mev-scroll flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-[1280px] px-4 py-6 md:px-6 md:py-8">
        {children}
      </div>
    </main>
  );
}