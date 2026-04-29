module arbiter(
    input  logic req0,
    input  logic req1,
    output logic gnt0,
    output logic gnt1
);

always_comb begin
    gnt0 = 1'b0;
    gnt1 = 1'b0;

    if (req0)
        gnt0 = 1'b1;
    if (req1)
        gnt1 = 1'b1;  
end

endmodule
