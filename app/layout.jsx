export const metadata = {
  title: 'SmartHire',
  description: 'Autonomous Interview Scheduler',
}

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
