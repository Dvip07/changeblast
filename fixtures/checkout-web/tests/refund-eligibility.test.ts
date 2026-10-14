import { render, screen } from "@testing-library/react";
import { RefundEligibility } from "../src/components/RefundEligibility";

const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();

test("hides the refund button for old purchases without calling the API", () => {
  global.fetch = jest.fn();
  render(<RefundEligibility subscriptionId="sub_1" purchasedAt={daysAgo(20)} />);
  expect(screen.getByText(/no longer eligible/)).toBeInTheDocument();
  expect(global.fetch).not.toHaveBeenCalled();
});
