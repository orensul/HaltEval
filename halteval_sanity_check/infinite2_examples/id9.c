int id1(unsigned int x);

void id9(unsigned int x)
{
  while (!id1(x))
    x += 2;
}
