/** Back navigation for recruiter drill-down screens (history-aware). */
export function recruiterGoBack(navigate, location, fallbackPath = '/recruiter/dashboard') {
  const returnTo = location.state?.returnTo;
  if (returnTo) {
    navigate(returnTo, { state: location.state?.returnState });
    return;
  }
  if (location.key !== 'default') {
    navigate(-1);
    return;
  }
  navigate(fallbackPath);
}
