import { redirect } from "next/navigation";

/**
 * /map regression route intact: redirects to expansion heatzone map view.
 */
export default function MapRegressionPage() {
  redirect("/w/expansion/heatzone");
}
